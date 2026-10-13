#!/usr/bin/env python3
"""Bounded periodic sampling previews. Pillow only; no ranking or decoding."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

MAX_PIXELS = 8_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def csv_values(text, cast):
    return list(dict.fromkeys(cast(x.strip()) for x in text.split(',')))


def separate(image, axis, period, phase, mode):
    width, height = image.size
    length = width if axis == 'cols' else height
    if phase >= length:
        return None
    count = (length - 1 - phase) // period + 1
    size = (count, height) if axis == 'cols' else (width, count)
    result = Image.new('RGB', image.size if mode == 'mask' else size, 'white')
    for index, position in enumerate(range(phase, length, period)):
        if axis == 'cols':
            strip = image.crop((position, 0, position + 1, height))
            result.paste(strip, (position if mode == 'mask' else index, 0))
        else:
            strip = image.crop((0, position, width, position + 1))
            result.paste(strip, (0, position if mode == 'mask' else index))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--periods', default='2,3,4,5,6,7,8')
    parser.add_argument('--axes', default='rows,cols')
    parser.add_argument('--phase', type=int)
    parser.add_argument('--mode', choices=['compact', 'mask'], default='compact')
    parser.add_argument('--save-candidates', action='store_true')
    args = parser.parse_args()
    try:
        periods = csv_values(args.periods, int)
        axes = csv_values(args.axes, str)
    except ValueError:
        parser.error('periods must be comma-separated integers')
    if not periods or any(p < 2 or p > 16 for p in periods):
        parser.error('periods must be between 2 and 16')
    if not axes or any(a not in ('rows', 'cols') for a in axes):
        parser.error('axes must be rows and/or cols')
    if args.phase is not None and any(not 0 <= args.phase < p for p in periods):
        parser.error('phase must be valid for every selected period')
    jobs = [(axis, p, phase) for axis in axes for p in periods
            for phase in ([args.phase] if args.phase is not None else range(p))]
    if args.save_candidates and len(jobs) > 32:
        parser.error('at most 32 native candidates; select fewer periods/phases')
    if args.image.stat().st_size > 32 * 1024 * 1024:
        parser.error('input exceeds 32 MiB')
    with Image.open(args.image) as source:
        if source.width * source.height > MAX_PIXELS or max(source.size) > 32768:
            parser.error('input exceeds 8 million pixels or 32768 pixels per dimension')
        # Render transparency explicitly; the original asset is never modified.
        rgba = source.convert('RGBA')
        image = Image.new('RGB', source.size, 'white')
        image.paste(rgba, mask=rgba.getchannel('A'))
    export_pixels = sum((image.width * image.height if args.mode == 'mask' else
                         math.ceil(max(0, (image.width if a == 'cols' else image.height) - phase) / p)
                         * (image.height if a == 'cols' else image.width)) for a, p, phase in jobs)
    if args.save_candidates and export_pixels > 32_000_000:
        parser.error('native exports exceed 32 million pixels; select fewer candidates')
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'source_sha256': hashlib.sha256(args.image.read_bytes()).hexdigest(),
                'source_size': list(image.size), 'mode': args.mode, 'alpha_background': 'white',
                'coverage': {'axes': axes, 'periods': periods, 'phase': args.phase},
                'candidates': [], 'sheets': [], 'interpretation': 'Unranked previews; inspect native resolution before drawing conclusions.'}
    cell_width, cell_height, per_sheet = 320, 180, 36
    sheet = None
    for ordinal, (axis, period, phase) in enumerate(jobs):
        slot = ordinal % per_sheet
        sheet_name = f'sheet-{ordinal // per_sheet + 1:02d}.png'
        if slot == 0:
            rows = math.ceil(min(per_sheet, len(jobs) - ordinal) / 4)
            sheet = Image.new('RGB', (cell_width * 4, cell_height * rows), '#dddddd')
            manifest['sheets'].append(sheet_name)
        candidate = separate(image, axis, period, phase, args.mode)
        entry = {'axis': axis, 'period': period, 'phase': phase, 'sheet': sheet_name, 'slot': slot}
        if candidate is None:
            entry['skipped'] = 'phase outside image dimension'
        else:
            entry['native_size'] = list(candidate.size)
            if args.save_candidates:
                filename = f'{axis}-p{period}-phase{phase}-{args.mode}.png'
                candidate.save(args.output / filename)
                entry['file'] = filename
            candidate.thumbnail((cell_width - 8, cell_height - 26), Image.Resampling.NEAREST)
        x, y = (slot % 4) * cell_width, (slot // 4) * cell_height
        ImageDraw.Draw(sheet).text((x + 4, y + 3), f'{axis} period={period} phase={phase}', fill='black')
        if candidate is not None:
            sheet.paste(candidate, (x + 4, y + 24))
        manifest['candidates'].append(entry)
        if slot == per_sheet - 1 or ordinal == len(jobs) - 1:
            sheet.save(args.output / sheet_name)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'manifest': 'manifest.json', 'sheets': manifest['sheets'],
                      'candidate_count': len(jobs), 'ranked': False}))


if __name__ == '__main__':
    main()
