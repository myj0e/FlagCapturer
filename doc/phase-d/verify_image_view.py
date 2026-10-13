"""Final image-view acceptance against the real offline Docker runtime."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path

from ctfbot.agent.loop import AgentLoop
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.codex_session import encode_tool_reply
from ctfbot.model_adapters.protocol import ToolCall, TurnResult
from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime
from ctfbot.tools.registry import ToolRegistry


FIXTURES = r'''
import os, pathlib, struct, zlib
from PIL import Image
root=pathlib.Path('/work')
image=Image.new('RGB',(12,8),(255,0,0))
for y in range(3,6):
    for x in range(2,7): image.putpixel((x,y),(0,255,0))
image.save(root/'pattern.png')
Image.new('RGBA',(3,2),(20,40,60,80)).save(root/'alpha.png')
frame=Image.new('RGB',(4,3),(0,0,255))
frame.save(root/'frames.gif',save_all=True,append_images=[Image.new('RGB',(4,3),(255,0,0))],duration=100,loop=0)
exif=Image.Exif(); exif[274]=6
Image.new('RGB',(4,2),(20,40,60)).save(root/'oriented.jpg',exif=exif)
Image.frombytes('RGB',(800,600),os.urandom(800*600*3)).save(root/'large.png')
Image.frombytes('RGB',(1100,800),os.urandom(1100*800*3)).save(root/'too-large.png')
(root/'bad.png').write_text('not an image')
(root/'link.png').symlink_to(root/'pattern.png')
(root/'link-dir').symlink_to(root,target_is_directory=True)
os.mkfifo(root/'fifo.png')
with (root/'oversized-source.png').open('wb') as stream: stream.truncate(32*1024*1024+1)
def chunk(kind,value):
    return struct.pack('!I',len(value))+kind+value+struct.pack('!I',zlib.crc32(kind+value))
(root/'bomb.png').write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',5000,5000,8,2,0,0,0))
                            +chunk(b'IDAT',zlib.compress(b''))+chunk(b'IEND',b''))
print('authored image fixtures ready')
'''


def verify(image: str, output: Path) -> None:
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    challenge, work = output / 'input', output / 'controller-work'
    challenge.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    # A small authored PPM avoids any controller-side imaging dependency.
    pixels = bytes([10, 20, 30]) * 6
    original = b'P6\n3 2\n255\n' + pixels
    (challenge / 'original.ppm').write_bytes(original)
    evidence = EvidenceStore(output / 'run')
    state = output / 'runtime-state'
    runtime = SupervisedOfflineRuntime(challenge, image, recovery_root=state,
                                      expected_daemon=daemon_identity(), event_sink=lambda _: None)
    checks = []
    tools = ToolRegistry(challenge, work, evidence, runtime)
    tools.supports_images = True
    tools.reliability.expected_inputs = {'original.ppm': hashlib.sha256(original).hexdigest()}

    def inspect(path, *, size, pixel=None, crop=None):
        arguments = {'path': path}
        if crop is not None:
            arguments['crop'] = crop
        outcome = tools.invoke(ToolCall('image_view', arguments))
        assert outcome.reply.success, outcome.reply.content
        assert len(outcome.reply.images) == 1
        data = outcome.reply.images[0].data
        ref = outcome.result['image_evidence']
        persisted, _ = evidence.read_artifact(ref['artifact'])
        assert persisted == data and ref['sha256'] == hashlib.sha256(data).hexdigest()
        assert outcome.result['view_size'] == size
        encoded = encode_tool_reply(outcome.reply, supports_images=True)
        image_item = encoded['contentItems'][1]
        assert image_item['type'] == 'inputImage'
        assert base64.b64decode(image_item['imageUrl'].split(',', 1)[1]) == data
        # Decode received bytes inside the actual sandbox and check pixels there.
        code = ("import base64,io,json,sys; from PIL import Image; "
                "image=Image.open(io.BytesIO(base64.b64decode(sys.argv[1]))); "
                "print(json.dumps({'size':list(image.size),'pixel':image.getpixel((0,0))}))")
        # Large images exceed argv; their validated hash/size and wire roundtrip
        # are checked above, without exceeding the command transport's argv cap.
        if len(data) < 64000:
            result = runtime.execute(['python3', '-c', code, base64.b64encode(data).decode()], timeout=30)
            assert result.exit_code == 0, result.stderr
            observed = json.loads(result.stdout)
            assert observed['size'] == size
            if pixel is not None:
                assert observed['pixel'] == pixel
        return outcome

    with runtime:
        result = runtime.execute(['python3', '-c', FIXTURES], timeout=30)
        assert result.exit_code == 0, result.stderr
        inspect('original.ppm', size=[3, 2], pixel=[10, 20, 30])
        checks.append('challenge_original_pixels_and_input_hash')
        inspect('/work/pattern.png', size=[12, 8], pixel=[255, 0, 0])
        checks.append('derived_image_original_dimensions')
        inspect('/work/pattern.png', size=[5, 3], pixel=[0, 255, 0],
                crop={'x': 2, 'y': 3, 'width': 5, 'height': 3})
        checks.append('crop_coordinates_dimensions_and_pixels')
        inspect('/work/alpha.png', size=[3, 2], pixel=[20, 40, 60, 80])
        checks.append('alpha_channel_preserved')
        frames = inspect('/work/frames.gif', size=[4, 3], pixel=[0, 0, 255])
        assert frames.result['frame_count'] == 2 and frames.result['frame'] == 0
        checks.append('animated_image_first_frame_and_frame_count')
        inspect('/work/oriented.jpg', size=[2, 4])
        checks.append('exif_orientation')
        large = inspect('/work/large.png', size=[800, 600])
        assert len(large.reply.images[0].data) > runtime.limits.command_output_bytes
        assert len(large.reply.content.encode()) <= tools.max_model_output_bytes
        checks.append('binary_payload_beyond_ordinary_command_and_text_caps')

        for path, crop in [('/work/pattern.png', {'x': 10, 'y': 0, 'width': 5, 'height': 2}),
                           ('/etc/passwd', None), ('../original.ppm', None),
                           ('/work/link.png', None), ('/work/link-dir/pattern.png', None),
                           ('/work/bad.png', None), ('/work/missing.png', None),
                           ('/work/fifo.png', None), ('/work/oversized-source.png', None),
                           ('/work/bomb.png', None), ('/work/too-large.png', None)]:
            arguments = {'path': path}
            if crop is not None:
                arguments['crop'] = crop
            outcome = tools.invoke(ToolCall('image_view', arguments))
            assert not outcome.reply.success and not outcome.reply.images, (path, outcome.reply.content)
        checks.append('invalid_crop_path_symlink_format_fifo_and_size_guards')
        # A failed view must not terminate or poison the existing runtime.
        inspect('/work/pattern.png', size=[12, 8])
        checks.append('failed_view_is_recoverable')
        tools.supports_images = False
        assert 'image_view' not in {spec.name for spec in tools.specs}
        denied = tools.invoke(ToolCall('image_view', {'path': 'original.ppm'}))
        assert denied.result['status'] == 'image_input_unsupported' and not denied.reply.images
        checks.append('text_only_model_disables_image_tool')
        tools.supports_images = True

        class AuthoredVisionModel:
            supports_images = True
            def run_turn(self, prompt, specs, dispatch, *, timeout):
                assert 'image_view' in {spec.name for spec in specs}
                for args in ({'path': 'original.ppm'}, {'path': '/work/pattern.png',
                              'crop': {'x': 2, 'y': 3, 'width': 5, 'height': 3}}):
                    reply = dispatch(ToolCall('image_view', args))
                    assert reply.success and reply.images
                    assert encode_tool_reply(reply, supports_images=True)['contentItems'][1]['type'] == 'inputImage'
                dispatch(ToolCall('run_complete', {"writeup": '# Authored solution\n\nReproduce the recorded fixture steps; outputs and submitted results are preserved in this run.', 'outcome': 'unsolved', 'summary': 'Authored vision acceptance', 'unresolved': []}))
                return TurnResult('Inspected authored images.', tool_calls=3)
            def close(self):
                pass
        loop = AgentLoop(AuthoredVisionModel(), tools, evidence,
                         challenge_provenance={'input_files': [{'path': 'original.ppm', 'sha256': hashlib.sha256(original).hexdigest()}]})
        run = loop.run('Inspect the authored original and a derived crop; this is not a CTF solution.')
        assert run.status == 'unsolved' and loop.transmission.image_count == 2
        checks.append('agent_loop_binary_delivery_and_telemetry')

    events = evidence.events_path.read_text()
    assert 'data:image/' not in events and 'data_base64' not in events
    checks.append('private_artifact_provenance_without_inline_log_payloads')
    assert not unfinished(state)
    assert subprocess.run(['docker', 'container', 'inspect', runtime.container_name], capture_output=True).returncode != 0
    write_private(output / 'acceptance.json', {'schema_version': 1, 'status': 'passed', 'image': image,
                  'checks': checks, 'resources_removed': True, 'real_model_used': False})
    print(output / 'acceptance.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    verify(args.image, args.output)
