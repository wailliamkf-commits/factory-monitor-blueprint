"""Read-only, sampled local recording inspection, never field acceptance.

Uses explicit pixel ROIs and source PTS. No cloud calls or invented alerts.
Private profiles and outputs must stay outside Git history.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop' / 'src'))
import cv2
import psutil
from factory_monitor_desktop.source_clock import ClockTracker, parse_camera_datetime


class LocalOCR:
    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, image, identifier):
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise ValueError('cannot encode ROI')
        message = {'id': identifier, 'image_base64': base64.b64encode(encoded).decode('ascii')}
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 5)[0]:
            self.close()
            raise TimeoutError('Local OCR deadline exceeded; stopped instead of overlapping calls')
        line = self.process.stdout.readline()
        response = json.loads(line)
        if response.get('id') != identifier or response.get('error'):
            raise RuntimeError('Local OCR response failed validation')
        return response

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)


def validate_rect(rect, iw, ih):
    if len(rect) != 4 or any(isinstance(x, bool) or not isinstance(x, int) for x in rect):
        raise ValueError('ROIs must be four integer pixel coordinates x,y,width,height')
    x, y, w, h = rect
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > iw or y + h > ih:
        raise ValueError('ROI is outside the calibrated image')


def pixel_crop(image, rect):
    ih, iw = image.shape[:2]
    validate_rect(rect, iw, ih)
    x, y, w, h = rect
    return image[y:y+h, x:x+w]


def validate_profile(profile):
    size = profile['size']
    if len(size) != 2 or any(type(x) is not int or not 1 <= x <= 16384 for x in size):
        raise ValueError('Invalid calibrated image size')
    seen, last_end = set(), -1.0
    segments = sorted(profile['segments'], key=lambda s: s['start'])
    if not segments:
        raise ValueError('At least one calibrated segment is required')
    for segment in segments:
        identity = segment['id']
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('Layout ids must be nonempty and unique')
        seen.add(identity)
        start, end = segment['start'], segment['end']
        if (any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                for x in (start, end)) or start < 0 or end <= start or start < last_end):
            raise ValueError('Layout intervals must be finite, ordered and disjoint')
        last_end = end
        cameras = set()
        if not segment['cameras']:
            raise ValueError('Layout needs at least one camera')
        for camera in segment['cameras']:
            identity = camera['id']
            if not isinstance(identity, str) or not identity or identity in cameras:
                raise ValueError('Camera ids must be nonempty and unique within each layout')
            cameras.add(identity)
            validate_rect(camera['crop'], *size)
            validate_rect(camera['clock'], *camera['crop'][2:])


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_person_model(weights, output):
    if not weights.is_file():
        raise ValueError('Local detector weights must already exist')
    os.environ['YOLO_OFFLINE'] = 'true'
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    isolated = (output / 'detector-settings').resolve()
    isolated.mkdir(parents=True, exist_ok=True)
    os.environ['YOLO_CONFIG_DIR'] = str(isolated)
    from ultralytics import YOLO, settings
    if not Path(settings.file).resolve().is_relative_to(isolated):
        raise RuntimeError('Detector settings are not isolated; use a fresh process')
    settings.update({'sync': False})
    if settings['sync'] is not False:
        raise RuntimeError('Detector telemetry was not disabled')
    model = YOLO(str(weights))
    if str(model.names.get(0, '')).casefold() != 'person':
        raise ValueError('Class 0 must be person; refuse to mislabel custom weights')
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ocr-helper', required=True, type=Path)
    parser.add_argument('--weights', type=Path)
    parser.add_argument('--step', type=float, default=2.0)
    parser.add_argument('--detect-every', type=float, default=30)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This diagnostic uses macOS Vision; a Windows OCR adapter is not implemented here')
    if not math.isfinite(args.step) or not 0.5 <= args.step <= 5:
        parser.error('--step must be 0.5..5 source seconds')
    if not math.isfinite(args.detect_every) or args.detect_every < args.step:
        parser.error('--detect-every must be finite and >= --step')
    profile = json.loads(args.profile.read_text())
    validate_profile(profile)
    args.output.mkdir(parents=True, exist_ok=False)
    cap = cv2.VideoCapture(str(args.input))
    if not cap.isOpened():
        raise RuntimeError('Input video is unreadable')
    fps = cap.get(cv2.CAP_PROP_FPS)
    count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if not math.isfinite(fps) or fps <= 0 or not math.isfinite(count) or count <= 0:
        raise ValueError('Missing finite source FPS/frame count')
    duration = count / fps
    # Deliberately no detector download or provider fallback.
    model = None
    if args.weights:
        # Apply before import, including connectivity checks and optional Sentry.
        # Keep settings isolated from the user's other YOLO projects.
        model = load_person_model(args.weights, args.output)
    ocr = LocalOCR(args.ocr_helper.resolve())
    trackers = {}
    statuses = Counter()
    total = readable = detections = 0
    next_detection = 0.0
    latencies = []
    max_rss = 0
    process = psutil.Process()
    started = time.monotonic()
    try:
        with (args.output / 'observations.jsonl').open('w') as output:
            for index in range(math.ceil(duration / args.step)):
                target = index * args.step
                cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000)
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f'Failed to decode scheduled sample at {target}s')
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if abs(pts-target) > max(.2, 2/fps):
                    raise RuntimeError('Seek did not return requested source timestamp')
                if [frame.shape[1], frame.shape[0]] != profile['size']:
                    raise ValueError('Source geometry changed; calibration invalid')
                intervals = [s for s in profile['segments'] if s['start'] <= pts < s['end']]
                if len(intervals) > 1:
                    raise ValueError('Ambiguous layout intervals')
                if not intervals:
                    trackers.clear()
                    row = {'pts': pts, 'state': 'layout_unknown', 'scope': 'recording_replay'}
                    output.write(json.dumps(row) + '\n')
                    statuses['layout_unknown'] += 1
                    continue
                segment = intervals[0]
                tiles = [pixel_crop(frame, c['crop']) for c in segment['cameras']]
                person_results = None
                if model is not None and pts >= next_detection:
                    before = time.monotonic()
                    person_results = model.predict(tiles, classes=[0], device='cpu', conf=.35, verbose=False)
                    latencies.append(time.monotonic()-before)
                    next_detection = pts + args.detect_every
                for j, camera in enumerate(segment['cameras']):
                    raw = pixel_crop(tiles[j], camera['clock'])
                    enlarged = cv2.resize(raw, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    padded = cv2.copyMakeBorder(enlarged, 20, 20, 20, 20,
                                               cv2.BORDER_CONSTANT, value=(80, 80, 80))
                    response = ocr.read(padded, f'{index}:{camera["id"]}')
                    observations = sorted(response['observations'], key=lambda o: o['box'][0])
                    text = ' '.join(o['text'] for o in observations)
                    value = parse_camera_datetime(text)
                    # OCR confidence is not ground truth. Raw observations retained for audit.
                    key = (segment['id'], camera['id'])
                    tracker = trackers.setdefault(key, ClockTracker())
                    state = tracker.observe(value, pts)
                    row = {'pts': pts, 'camera': camera['id'], 'layout': segment['id'],
                           'state': state, 'clock_text': text,
                           'clock': value.isoformat() if value else None,
                           'ocr': response, 'tile_size': [tiles[j].shape[1], tiles[j].shape[0]],
                           'scope': 'sampled_recording_replay', 'behavior': 'not_evaluated'}
                    if person_results is not None:
                        result = person_results[j]
                        row['person_boxes'] = result.boxes.xyxy.tolist()
                        row['person_scores'] = result.boxes.conf.tolist()
                        detections += len(result.boxes)
                        # Limit illustrative images; no speculative behavioral labels.
                        if index < 2 or (len(result.boxes) and index < 100):
                            cv2.imwrite(str(args.output / f'person-{index}-{j}.jpg'), result.plot())
                    total += 1
                    readable += value is not None
                    statuses[state] += 1
                    output.write(json.dumps(row, ensure_ascii=False) + '\n')
                max_rss = max(max_rss, sum(p.memory_info().rss for p in [process, *process.children()] if p.is_running()))
                if index % 60 == 0:
                    print(json.dumps({'processed_source_seconds':round(pts,2), 'readable':readable,
                                      'samples':total}), flush=True)
    finally:
        cap.release()
        ocr.close()
    ordered = sorted(latencies)
    report = {'scope':'sampled real recording; not live cameras, not exhaustive action review',
              'source_sha256': file_sha256(args.input),
              'profile_sha256': file_sha256(args.profile),
              'duration_seconds':duration, 'source_fps':fps, 'sample_step_seconds':args.step,
              'detection_sample_seconds':args.detect_every,
              'clock_samples':total, 'parseable_clock_samples':readable,
              'statuses':dict(statuses), 'person_box_occurrences':detections,
              'detector_batch_count':len(latencies), 'detector_device':'cpu',
              'detector_batch_seconds_median': ordered[len(ordered)//2] if ordered else None,
              'detector_batch_seconds_p95': ordered[min(len(ordered)-1, math.ceil(len(ordered)*.95)-1)] if ordered else None,
              'elapsed_seconds':time.monotonic()-started, 'sampled_max_process_tree_rss_bytes':max_rss,
              'clock_backend':'Apple Vision local diagnostic; not Chinese production OCR',
              'memory_measurement_scope':'Sampled process-tree RSS during analysis only; not full machine/GPU peak',
              'acceptance':'NOT_FIELD_ACCEPTED', 'layout_selection':'manual source-PTS intervals, not automatic learning',
              'person_box_occurrences_note':'Repeated boxes are not unique people or accuracy evidence',
              'no_detection_note':'No detection never proves no person; small and occluded people may be missed'}
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
