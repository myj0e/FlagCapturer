import { readRegular, invariant, atomicWrite, json, confined } from '../util.js';
import { runProcess } from '../runtime/process.js';
import { join } from 'node:path';
export async function recover(root, runner = runProcess) {
  const path = await confined(root, 'recovery.json');
  const receipt = JSON.parse((await readRegular(path, 65536)).toString());
  invariant(receipt.tool_containers === undefined || Array.isArray(receipt.tool_containers) && receipt.tool_containers.length <= 64, 'Invalid tool container receipt');
  const names = [...new Set([receipt.container, ...(receipt.service_container ? [receipt.service_container] : []), ...(receipt.tool_containers ?? [])])];
  invariant(receipt.schema_version === 1 && names.every(name => /^ctfbot-[a-f0-9-]{36}$/.test(name)) && /^[a-f0-9-]{36}$/.test(receipt.run_id), 'Invalid recovery receipt');
  if (!receipt.pending) return {
    recovered: true,
    already_closed: true
  };
  for (const name of names) {
    const inspected = await runner('docker', ['inspect', '--format', '{{json .Config.Labels}}', name]);
    if (inspected.code !== 0) {
      invariant(inspected.stderr.toString().includes('No such'), 'Docker ownership cannot be inspected');
    } else {
      const labels = JSON.parse(inspected.stdout.toString());
      invariant(labels['flagcapturer.run'] === receipt.run_id, 'Container ownership mismatch; refusing deletion');
      const result = await runner('docker', ['rm', '-f', name]);
      invariant(result.code === 0, 'Container removal unconfirmed');
    }
  }
  await atomicWrite(join(root, 'recovery.json'), json({
    ...receipt,
    pending: false
  }));
  return {
    recovered: true,
    containers: names
  };
}
