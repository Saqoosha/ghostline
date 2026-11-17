"""Run every feed of jobs.json through udgp_feed.sh on this Vast instance, PER feeds at a time on each GPU (each feed runs
two trackers, so 2 feeds keep a 4090 busy while the next one cuts its videos). A feed starts once its original has fully
arrived (src/<file>.ok, written by the uploader after its rsync); a feed with feeds/<name>/done.json is skipped.
  /opt/env/bin/python /root/udgp/tools/udgp_queue.py /root/udgp/jobs.json        env PER (2)"""
import json, os, subprocess, sys, threading, time

R = "/root/udgp"; jobs = json.load(open(sys.argv[1])); PER = int(os.environ.get("PER", 2))
ngpu = int(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.count("GPU "))
todo = [j for j in jobs if not os.path.exists(f"{R}/feeds/{j['name']}/done.json")]
lock = threading.Lock()
print(f"{len(todo)} feeds to run on {ngpu} GPUs, {PER} each", flush=True)


def take():
    while True:
        with lock:
            if not todo: return None
            for j in todo:
                if os.path.exists(f"{R}/src/{j['src']}.ok"): todo.remove(j); return j
        time.sleep(10)                                   # nothing has arrived yet


def worker(gpu):
    while (j := take()) is not None:
        t = time.time()
        r = subprocess.run(["bash", f"{R}/tools/udgp_feed.sh", j["name"], j["src"], str(j["start"]), str(j["dur"]), str(j["slot"])],
                           env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu)), capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or [""])[-1] if r.returncode == 0 else "FAILED " + r.stderr.strip()[-400:]
        print(f"gpu {gpu} {j['name']} ({j['pilot']}) {time.time() - t:.0f} s: {tail}", flush=True)


ts = [threading.Thread(target=worker, args=(g,)) for g in range(ngpu) for _ in range(PER)]
for t in ts: t.start()
for t in ts: t.join()
print("QUEUE-DONE", flush=True)
