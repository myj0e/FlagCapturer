import { s } from './schema.js';
import { invariant, base64 } from '../util.js';
export function installImages(registry, {
  supportsImages,
  runtime,
  inputBytes
}) {
  const {
    evidence
  } = registry;
  const define = registry.define.bind(registry);
  if (supportsImages) define('image_view', 'View real image pixels from /challenge or /work; Pillow decodes in sandbox. Optional EXIF-oriented crop; no automatic resizing.', s.object({
    path: s.text(512),
    crop: s.object({
      x: s.int(0, 100000),
      y: s.int(0, 100000),
      width: s.int(1, 100000),
      height: s.int(1, 100000)
    })
  }, ['path']), async ({
    path,
    crop
  }, signal) => {
    invariant(path.startsWith('/challenge/') || path.startsWith('/work/'), 'Expected sandbox image path');
    invariant(!path.split('/').includes('..'), 'Path traversal forbidden');
    if (path.startsWith('/challenge/')) await inputBytes(path.slice(11));
    const source = `import sys,json,pathlib,io,base64
from PIL import Image,ImageOps
p=pathlib.Path(sys.argv[1]); assert p.resolve().is_relative_to('/challenge') or p.resolve().is_relative_to('/work'); assert p.stat().st_size<=33554432
Image.MAX_IMAGE_PIXELS=16000000
im=ImageOps.exif_transpose(Image.open(p))
crop=json.loads(sys.argv[2])
if crop:
 x,y,w,h=[crop[k] for k in ('x','y','width','height')]; assert x+w<=im.width and y+h<=im.height; im=im.crop((x,y,x+w,y+h))
assert im.width*im.height<=4000000
im=im.convert('RGB'); out=io.BytesIO(); im.save(out,format='PNG'); data=out.getvalue(); assert len(data)<=1048576
print(json.dumps({'data':base64.b64encode(data).decode(),'width':im.width,'height':im.height}))`;
    const result = await runtime.execute(['python3', '-I', '-c', source, path, JSON.stringify(crop ?? null)], {
      signal,
      limit: 2 * 1024 * 1024
    });
    invariant(result.code === 0 && !result.truncated, 'Image rendering failed; crop or create a smaller derived image');
    const image = JSON.parse(result.stdout.toString());
    const artifact = await evidence.artifact(base64(image.data), 'image/png');
    return {
      image: {
        type: 'image',
        data: image.data,
        mimeType: 'image/png'
      },
      width: image.width,
      height: image.height,
      artifact
    };
  }, {
    observe: true
  });
}
