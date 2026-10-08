'''CLI dispatch for the baseline project.'''

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ctfbot import __version__
from ctfbot.agent.loop import RunLimits
from ctfbot.application.baseline import BaselineAdmissionError
from ctfbot.application.baseline_smoke import run_synthetic_smoke
from ctfbot.application.diagnostics import collect_diagnostics, render_diagnostics
from ctfbot.application.llm_setup import setup_llm, show_llm_status, test_llm_connection, test_llm_tool_call
from ctfbot.application.llm_setup import read_llm_config
from ctfbot.application.service import create_codex_application_service
from ctfbot.benchmark.runner import load_case_set, run_batch
from ctfbot.model_adapters.codex_session import CodexModelSession
from ctfbot.tui.app import run_tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ctfbot',
        description='Tool-driven CTF solving workbench for LLM agents.',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}',
    )
    parser.add_argument('--service-profile', type=Path, help='controller-owned local service profile (default: data/service-profile.json)')
    parser.add_argument('--remote-profile', type=Path, help='reviewed remote runtime profile (default: data/remote-profile.json)')
    parser.add_argument('--remote-grant', type=Path, help='private remote target grant (default: profile directory/remote-grant.json)')
    parser.add_argument('--memory-root', type=Path, help='private controller memory store; no memory is read by default')
    parser.add_argument('--memory-namespace', action='append', choices=['manuals', 'failures', 'challenge', 'writeups'], default=[])
    parser.add_argument('--evaluation-mode', choices=['blind', 'practice'], default='blind')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('doctor', help='show local project and runtime diagnostics')
    packs = subparsers.add_parser('packs', help='list/read built-in domain workflows without launching a runtime')
    packs.add_argument('pack', nargs='?', choices=['crypto', 'forensics', 'stego', 'reverse', 'pwn', 'web'])
    fixtures = subparsers.add_parser('fixtures', help='create private authored six-category development/holdout inputs; never runs them')
    fixtures.add_argument('--output', required=True, type=Path)
    fixtures.add_argument('--split', choices=['development', 'holdout'], default='development')
    fixtures.add_argument('--suite', choices=['core', 'expanded'], default='core', help='six original or six additional authored mechanisms')
    fixtures.add_argument('--authorize-model-data', action='store_true')
    memory = subparsers.add_parser('memory', help='explicitly propose/review/revoke memory; never uses a model')
    memory_commands = memory.add_subparsers(dest='memory_command', required=True)
    proposal = memory_commands.add_parser('propose')
    proposal.add_argument('--source-file', required=True, type=Path)
    proposal.add_argument('--namespace', required=True, choices=['manuals', 'failures', 'challenge', 'writeups'])
    proposal.add_argument('--source-url', required=True)
    proposal.add_argument('--license', required=True)
    proposal.add_argument('--tag', action='append', default=[])
    proposal.add_argument('--challenge-id')
    review = memory_commands.add_parser('review')
    review.add_argument('--id', required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--basis', required=True)
    review.add_argument('--decision', required=True, choices=['approve', 'reject'])
    review.add_argument('--contamination', required=True, choices=['generic', 'challenge_specific', 'answer'])
    revoke = memory_commands.add_parser('revoke')
    revoke.add_argument('--id', required=True)
    revoke.add_argument('--basis', required=True)
    audit = subparsers.add_parser('audit-run', help='verify persisted artifact hashes and event ordering; does not execute commands')
    audit.add_argument('--run-dir', required=True, type=Path)
    diagnose = subparsers.add_parser('diagnose-run', help='read-only provenance/continuation/tool diagnostic index; no model or challenge execution')
    diagnose.add_argument('--run-dir', required=True, type=Path)
    replay = subparsers.add_parser('replay', help='explicit offline command replay in Docker, without a model or flag submission')
    replay.add_argument('--run-dir', required=True, type=Path)
    replay.add_argument('--workspace', required=True, type=Path)
    replay.add_argument('--oracle', type=Path, help='optional known-answer oracle; not used to verify replay output')
    replay.add_argument('--output', required=True, type=Path)
    replay.add_argument('--wall-time', type=int, default=300)
    replay_recover = subparsers.add_parser('replay-recover', help='recover owned offline replay containers; retain uncertain Docker requests')
    replay_recover.add_argument('--output', required=True, type=Path)
    evaluation = subparsers.add_parser('evaluate', help='bounded version-2 dataset evaluation through the shared service (uses model quota)')
    evaluation.add_argument('--dataset', required=True, type=Path)
    evaluation.add_argument('--development-reference', type=Path)
    evaluation.add_argument('--runtime-image', required=True)
    evaluation.add_argument('--output', required=True, type=Path)
    evaluation.add_argument('--max-total-turns', required=True, type=int)
    evaluation.add_argument('--max-total-tool-calls', required=True, type=int)
    evaluation.add_argument('--max-total-wall-time', type=int, default=3600)
    evaluation.add_argument('--repetitions', type=int, default=1)
    evaluation.add_argument('--confirm-model-usage', action='store_true')
    split = subparsers.add_parser('audit-split', help='compare versioned development/holdout case identities and input hashes')
    split.add_argument('--development', required=True, type=Path)
    split.add_argument('--holdout', required=True, type=Path)
    service_parser = subparsers.add_parser('service', help='review a local service profile or recover its resources')
    service_commands = service_parser.add_subparsers(dest='service_command', required=True)
    approve = service_commands.add_parser('approve', help='activate the authored fixture from current complete acceptance evidence')
    approve.add_argument('--acceptance', required=True, type=Path)
    approve.add_argument('--basis', required=True, help='record the review and execution authorization basis')
    service_commands.add_parser('status', help='show the local profile and unfinished recovery records')
    service_commands.add_parser('recover', help='remove owned resources; clear recovery only after Docker acknowledgement')
    remote_parser = subparsers.add_parser('remote', help='review a remote TCP profile or recover its solver')
    remote_commands = remote_parser.add_subparsers(dest='remote_command', required=True)
    remote_approve = remote_commands.add_parser('approve', help='activate only the scope covered by complete C4 acceptance')
    remote_approve.add_argument('--acceptance', required=True, type=Path)
    remote_approve.add_argument('--basis', required=True)
    remote_commands.add_parser('status', help='show remote activation and unfinished recovery records')
    remote_commands.add_parser('recover', help='recover owned offline solvers without clearing uncertain requests')
    llm_parser = subparsers.add_parser('llm', help='configure and inspect the LLM connection')
    llm_subparsers = llm_parser.add_subparsers(dest='llm_command', required=True)
    llm_subparsers.add_parser('setup', help='sign in with ChatGPT and select a Codex model')
    llm_subparsers.add_parser('status', help='show the saved model and current Codex sign-in')
    llm_subparsers.add_parser('test', help='send one short model connection check')
    llm_subparsers.add_parser('tool-smoke', help='make one experimental constant-return tool-call check')
    solve = subparsers.add_parser('solve', help='run one admitted offline or reviewed service/remote challenge (uses model quota)')
    solve.add_argument('--workspace', required=True, help='importer-generated workspace directory')
    solve.add_argument('--oracle', help='optional controller-only oracle.json for known-answer verification')
    solve.add_argument('--additional-prompt', default='', help='optional solving hints, including any known flag format; no format check is performed')
    solve.add_argument('--runtime-image', required=True, help='tool image pinned by repository digest or sha256 image ID')
    solve.add_argument('--runs-root', default='runs/phase-a', help='private output root (default: runs/phase-a)')
    solve.add_argument('--max-turns', type=int, default=30)
    solve.add_argument('--max-tool-calls', type=int, default=None, help='Optional explicit per-run cap; default: unlimited')
    solve.add_argument('--wall-time', type=int, default=1800, help='maximum run time in seconds')
    solve.add_argument('--confirm-model-usage', action='store_true', help='confirm this command may use model quota')
    batch = subparsers.add_parser('baseline', help='run the admitted pilot batch (uses model quota)')
    batch.add_argument('--case-set', required=True, help='private JSON case-set with admitted workspace/oracle paths')
    batch.add_argument('--runtime-image', required=True, help='tool image pinned by repository digest or sha256 image ID')
    batch.add_argument('--runs-root', default='runs/phase-a', help='private output root (default: runs/phase-a)')
    batch.add_argument('--max-total-model-turns', type=int, required=True,
                       help='hard batch-wide model-turn cap (maximum 690)')
    batch.add_argument('--confirm-model-usage', action='store_true', help='confirm this command may use model quota')
    batch.add_argument('--max-turns', type=int, default=30)
    batch.add_argument('--max-tool-calls', type=int, default=None, help='Optional explicit per-run cap; default: unlimited')
    batch.add_argument('--wall-time', type=int, default=1800)
    subparsers.add_parser('baseline-smoke', help='run the synthetic offline agent-loop acceptance smoke')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {'packs', 'fixtures', 'memory', 'audit-run', 'diagnose-run', 'audit-split', 'replay', 'replay-recover', 'evaluate'}:
        try:
            if args.command == 'packs':
                from ctfbot.packs.catalog import catalog, playbook
                print(playbook(args.pack) if args.pack else json.dumps(catalog(), indent=2))
                return 0
            if args.command == 'fixtures':
                from ctfbot.challenge.domain_fixtures import create_domain_dataset
                print(create_domain_dataset(args.output, authorize_model_data=args.authorize_model_data, split=args.split, suite=args.suite))
                return 0
            if args.command == 'memory':
                from ctfbot.application.memory import propose, review, revoke
                if args.memory_root is None:
                    raise ValueError('memory operations require an explicit --memory-root before the subcommand')
                if args.memory_command == 'propose':
                    print(propose(args.memory_root, args.source_file, namespace=args.namespace, source_url=args.source_url,
                                  license=args.license, tags=args.tag, challenge_id=args.challenge_id))
                elif args.memory_command == 'review':
                    print(review(args.memory_root, args.id, reviewer=args.reviewer, basis=args.basis,
                                 decision=args.decision, contamination=args.contamination))
                else:
                    revoke(args.memory_root, args.id, basis=args.basis)
                    print('Memory version revoked for subsequent runs; existing run snapshots remain recorded.')
                return 0
            if args.command == 'audit-run':
                from ctfbot.reporting.replay import audit_run
                print(json.dumps(audit_run(args.run_dir), indent=2))
                return 0
            if args.command == 'diagnose-run':
                from ctfbot.reporting.diagnosis import diagnose_run
                print(json.dumps(diagnose_run(args.run_dir), indent=2, ensure_ascii=True))
                return 0
            if args.command == 'audit-split':
                from ctfbot.benchmark.evaluation import audit_separation
                result = audit_separation(args.development, args.holdout)
                print(json.dumps(result, indent=2))
                return 0 if result['disjoint'] else 1
            if args.command == 'replay':
                from ctfbot.reporting.replay import replay_run
                saved = replay_run(run_dir=args.run_dir, workspace=args.workspace, oracle=args.oracle,
                                   output=args.output, wall_seconds=args.wall_time)
                print(saved)
                return 0 if json.loads(saved.read_text())['status'] == 'matched' else 1
            if args.command == 'replay-recover':
                from ctfbot.runtime.service_recovery import recover
                results = recover(args.output / 'replay-state')
                print(json.dumps(results, indent=2))
                return 1 if any(result['status'] != 'cleaned' for result in results) else 0
            if not args.confirm_model_usage:
                raise ValueError('evaluate requires --confirm-model-usage; model quota has not been authorized')
            from ctfbot.benchmark.evaluation import run_evaluation
            service = create_codex_application_service(service_profile=args.service_profile, remote_profile=args.remote_profile,
                                                       remote_grant=args.remote_grant, memory_root=args.memory_root,
                                                       memory_namespaces=tuple(args.memory_namespace), evaluation_mode=args.evaluation_mode)
            result = run_evaluation(dataset_path=args.dataset, service=service, runtime_image=args.runtime_image,
                                    output=args.output, limits=RunLimits(), max_total_turns=args.max_total_turns,
                                    max_total_tool_calls=args.max_total_tool_calls, max_total_wall_seconds=args.max_total_wall_time,
                                    repetitions=args.repetitions, development_reference=args.development_reference)
            print(result)
            return 0 if json.loads(result.read_text())['status'] == 'complete' else 1
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'{args.command} failed: {exc}')
            return 1

    if args.command == 'remote':
        from ctfbot.application.remote_profile import ReviewedRemoteFactory, approve_remote_profile, remote_profile_path
        from ctfbot.challenge.remote import RemoteSpec
        from ctfbot.runtime.service_recovery import read_private, recover, unfinished
        path = args.remote_profile or remote_profile_path()
        grant_path = args.remote_grant or path.parent / 'remote-grant.json'
        try:
            if args.remote_command == 'recover':
                results = recover(path.parent / 'remote-state')
                print(json.dumps(results, indent=2))
                return 1 if any(result['status'] != 'cleaned' for result in results) else 0
            if args.remote_command == 'approve':
                saved = approve_remote_profile(args.acceptance, grant_path, args.basis, destination=path)
                print(f'Reviewed remote profile saved: {saved}')
                print('Only the exact grant, pinned IP, time window, solver image and Docker host are enabled.')
                return 0
            pending = unfinished(path.parent / 'remote-state')
            print(f'Unfinished remote recovery records: {len(pending)}')
            if not path.exists():
                print(f'Remote execution disabled: no reviewed profile at {path}')
                return 1 if pending else 0
            grant = read_private(grant_path)
            spec = RemoteSpec.from_manifest(grant.get('remote'))
            ReviewedRemoteFactory(path, grant_path).validate(spec, grant.get('solver_image'))
            print(f'Reviewed profile: {path}')
            print(f'Authorized endpoint: {spec.endpoint}; pinned IP: {grant.get("pinned_ip")}')
            print(f'Authorization expires: {spec.not_after.isoformat()}')
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'Remote {args.remote_command} failed: {exc}')
            return 1

    if args.command == 'service':
        from ctfbot.application.service_profile import ReviewedServiceFactory, approve_service_profile, service_profile_path
        from ctfbot.challenge.local_service import LocalServiceSpec
        from ctfbot.runtime.service_recovery import daemon_identity, read_private, recover, unfinished
        path = args.service_profile or service_profile_path()
        try:
            if args.service_command == 'approve':
                saved = approve_service_profile(args.acceptance, args.basis, destination=path)
                print(f'Reviewed local service profile saved: {saved}')
                print('Only its exact images, command, port and Docker host are enabled. Model-data authorization is still required.')
                return 0
            if args.service_command == 'recover':
                results = recover(path.parent / 'service-state')
                print(json.dumps(results, indent=2))
                return 1 if any(result['status'] != 'cleaned' for result in results) else 0
            if not path.exists():
                print(f'Local services disabled: no reviewed profile at {path}')
                return 0
            profile = read_private(path)
            pending = unfinished(path.parent / 'service-state')
            matches = profile.get('daemon') == daemon_identity()
            print(f'Profile: {path}')
            print(f'Service image: {profile.get("service_image")}')
            print(f'Solver image: {profile.get("solver_image")}')
            print(f'Docker host matches acceptance: {matches}')
            print(f'Unfinished recovery records: {len(pending)}')
            spec = LocalServiceSpec.from_manifest({
                'schema_version': 1, 'image': profile.get('service_image'),
                'argv': profile.get('service_argv'), 'port': profile.get('service_port'),
                'startup_timeout_seconds': 15, 'runtime_authorized': True,
                'authorization_basis': profile.get('authorization_basis'),
            })
            ReviewedServiceFactory(path).validate(spec, profile.get('solver_image'))
            return 0 if matches and not pending else 1
        except (OSError, ValueError, RuntimeError) as exc:
            print(f'Local service {args.service_command} failed: {exc}')
            return 1

    if args.command == 'doctor':
        diagnostics = collect_diagnostics()
        render_diagnostics(diagnostics)
        return 1 if any(item.status == 'FAIL' for item in diagnostics) else 0

    if args.command == 'llm':
        if args.llm_command == 'setup':
            return setup_llm()
        if args.llm_command == 'status':
            return show_llm_status()
        if args.llm_command == 'test':
            return test_llm_connection()
        if args.llm_command == 'tool-smoke':
            return test_llm_tool_call()
        return 2

    if args.command == 'baseline-smoke':
        passed, run_dir = run_synthetic_smoke()
        print(f'Synthetic baseline smoke: {"PASS" if passed else "FAIL"}')
        print(f'Synthetic evidence: {run_dir}')
        return 0 if passed else 1

    if args.command == 'solve':
        try:
            if not args.confirm_model_usage:
                print('Refusing to start: add --confirm-model-usage to explicitly authorize model quota use.')
                return 2
            workspace = Path(args.workspace)
            oracle_path = Path(args.oracle) if args.oracle else None
            runs_root = Path(args.runs_root)
            limits = RunLimits(
                max_turns=args.max_turns,
                max_tool_calls=args.max_tool_calls,
                wall_time_seconds=args.wall_time,
            )
            service = create_codex_application_service(service_profile=args.service_profile,
                                                       remote_profile=args.remote_profile, remote_grant=args.remote_grant,
                                                       memory_root=args.memory_root, memory_namespaces=tuple(args.memory_namespace),
                                                       evaluation_mode=args.evaluation_mode)
            result = service.run(
                workspace=workspace,
                oracle_path=oracle_path,
                runtime_image=args.runtime_image,
                runs_root=runs_root,
                limits=limits,
                additional_prompt=args.additional_prompt,
            )
        except (BaselineAdmissionError, OSError, ValueError, RuntimeError) as exc:
            print(f'Pilot run failed before completion: {exc}')
            return 1
        print(f'Run {result.run_id}: {result.status} ({result.stop_reason})')
        print(f'Turns: {result.turns}; tool calls: {result.tool_calls}')
        print(f'Evidence: {result.run_dir}')
        if result.status == 'format_only':
            print('Candidate matches the expected format; correctness remains unverified.')
            return 0
        if result.status == 'candidate_unverified':
            print('Attempt completed with an unverified candidate; correctness remains unverified.')
            return 0
        return 0 if result.verified else 1

    if args.command == 'baseline':
        if args.memory_namespace or args.memory_root or args.evaluation_mode != 'blind':
            print('Legacy Stage A baseline does not accept memory options; use evaluate for explicit D-stage conditions.')
            return 1
        try:
            if not args.confirm_model_usage:
                print('Refusing to start: add --confirm-model-usage to explicitly authorize model quota use.')
                return 2
            config = read_llm_config()
            if not config:
                parser.error('no model is configured; run `ctfbot llm setup` first')
            cases, repeat_ids = load_case_set(Path(args.case_set))
            limits = RunLimits(
                max_turns=args.max_turns,
                max_tool_calls=args.max_tool_calls,
                wall_time_seconds=args.wall_time,
            )
            metadata = {
                'provider': str(config['provider']),
                'model': str(config['model']),
                'reasoning_effort': config.get('reasoning_effort'),
                'provider_api': 'Codex App Server experimental dynamicTools',
            }
            batch_result = run_batch(
                cases=cases,
                repeat_challenges=repeat_ids,
                model_factory=lambda: CodexModelSession(
                    str(config['model']),
                    str(config['reasoning_effort']) if config.get('reasoning_effort') else None,
                ),
                model_metadata=metadata,
                runtime_image=args.runtime_image,
                runs_root=Path(args.runs_root),
                limits=limits,
                max_total_model_turns=args.max_total_model_turns,
            )
        except (BaselineAdmissionError, OSError, ValueError, RuntimeError) as exc:
            print(f'Baseline batch failed before or during a run: {exc}')
            return 1
        print(f'Batch {batch_result.batch_id}: {batch_result.status}')
        print(f'Runs: {batch_result.completed_runs}; verified: {batch_result.verified_runs}')
        print(f'Remaining model turns: {batch_result.remaining_model_turns}')
        print(f'Summary: {batch_result.summary_path}')
        return 0 if batch_result.status == 'complete' else 1

    return run_tui(service_profile=args.service_profile, remote_profile=args.remote_profile,
                   remote_grant=args.remote_grant, memory_root=args.memory_root,
                   memory_namespaces=tuple(args.memory_namespace), evaluation_mode=args.evaluation_mode)
