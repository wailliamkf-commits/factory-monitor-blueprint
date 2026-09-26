"""Bounded macOS capture of one explicitly named PID/window. No UI control.

Images and raw logs are local/private. Changing pixels alone is not proof of
camera freshness; use known playback/pause controls or independently read clocks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import cv2
import numpy as np

HEADER = struct.Struct('>ddIIII')


def read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        value = stream.read(size-len(chunks))
        if not value:
            raise EOFError('Native capture stream ended')
        chunks.extend(value)
    return bytes(chunks)


def read_packet(stream):
    callback, pts, status, w, h, length = HEADER.unpack(read_exact(stream, HEADER.size))
    if not (0 < w <= 4096 and 0 < h <= 4096 and w*h <= 8_388_608 and length == w*h*4):
        raise ValueError('Invalid native image packet')
    image = np.frombuffer(read_exact(stream,length),dtype=np.uint8).reshape(h,w,4)
    if status != 0:
        raise ValueError('Native frame was not complete')
    return callback, pts, image[:,:,:3].copy()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',required=True,type=int)
    parser.add_argument('--title',required=True)
    parser.add_argument('--width',required=True,type=int)
    parser.add_argument('--height',required=True,type=int)
    parser.add_argument('--seconds',default=30,type=int)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if sys.platform!='darwin': parser.error('macOS ScreenCaptureKit is required')
    if not 1<=args.seconds<=60: parser.error('duration must be 1..60 seconds')
    if args.pid<=0 or not args.title.strip(): parser.error('positive PID and exact title required')
    if not (0<args.width<=4096 and 0<args.height<=4096 and args.width*args.height<=8_388_608):
        parser.error('output geometry must be positive and at most 8 megapixels')
    args.output.mkdir(parents=True,exist_ok=False)
    helper=args.output.resolve()/'capture-window'
    source=Path(__file__).with_name('CaptureWindow.swift')
    subprocess.run(['xcrun','swiftc','-O','-parse-as-library',str(source),'-o',str(helper),
                    '-framework','ScreenCaptureKit','-framework','AppKit','-framework','CoreMedia',
                    '-framework','CoreVideo'],check=True,capture_output=True,timeout=90)
    inbox=queue.Queue(maxsize=2)
    stop=threading.Event()
    dropped=[0]
    with (args.output/'native.stderr.log').open('wb') as errors:
        process=subprocess.Popen([str(helper),str(args.pid),args.title,str(args.width),str(args.height)],
                                 stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=errors,bufsize=0)
        def receive():
            try:
                while not stop.is_set():
                    packet=read_packet(process.stdout)
                    try: inbox.put_nowait(packet)
                    except queue.Full: dropped[0]+=1
            except Exception as exc:
                if not stop.is_set():
                    try: inbox.put_nowait(str(exc))
                    except queue.Full: pass
        reader=threading.Thread(target=receive,daemon=True)
        reader.start()
        begin=time.monotonic();last_frame=begin;count=0;failure=None;previous=None;hashes=set()
        try:
            with (args.output/'frames.jsonl').open('w') as log:
                while time.monotonic()-begin<args.seconds:
                    try: packet=inbox.get(timeout=.2)
                    except queue.Empty:
                        if time.monotonic()-last_frame>5:
                            failure='No complete target-window frame for five seconds';break
                        continue
                    if isinstance(packet,str): failure=packet;break
                    callback,pts,image=packet
                    elapsed=time.monotonic()-begin;last_frame=time.monotonic()
                    h,w=image.shape[:2];roi=image[round(h*.22):round(h*.78),round(w*.22):round(w*.78)]
                    digest=hashlib.sha256(roi.tobytes()).hexdigest();hashes.add(digest)
                    delta=None if previous is None else float(np.mean(cv2.absdiff(roi,previous)))
                    previous=roi.copy();count+=1
                    row={'frame':count,'elapsed':elapsed,'callback_wall':callback,'sck_sample_pts':pts,
                         'roi_hash':digest,'mean_absolute_pixel_delta':delta}
                    log.write(json.dumps(row)+'\n')
                    if count==1 or count%25==0: cv2.imwrite(str(args.output/f'window-{count:04}.png'),image)
        finally:
            stop.set();process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=2)
            reader.join(timeout=2)
            if process.stdout: process.stdout.close()
        report={'scope':'OS exact-window capture; no claim of camera freshness or field acceptance',
                'frames':count,'unique_center_roi_hashes':len(hashes),'consumer_dropped':dropped[0],
                'failure':failure,'elapsed_seconds':time.monotonic()-begin,
                'gate':'FAIL' if failure or count==0 else 'PIXELS_RECEIVED_ONLY'}
        (args.output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        return 1 if report['gate']=='FAIL' else 0

if __name__=='__main__':
    raise SystemExit(main())
