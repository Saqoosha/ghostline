"""Scan (Avata 2) camera poses in the web frame (x east, y up, z south), plus the gate circles."""
import json, sys, re, numpy as np
g = json.load(open(sys.argv[2])); s, R, t = g["scale"], np.array(g["R"]), np.array(g["t"]); Mz = np.diag([1.0, 1.0, -1.0])
def q2R(qw, qx, qy, qz):
    return np.array([[1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],[2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],[2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)]])
def R_to_q(R):                                          # -> x,y,z,w
    from scipy.spatial.transform import Rotation as Rot; return Rot.from_matrix(R).as_quat().round(6).tolist()
cams = []
for line in open(sys.argv[1]):
    p = line.split(); Rc = q2R(*map(float, p[1:5])); tv = np.array(list(map(float, p[5:8]))); name = p[9]
    m = re.match(r"([ab])_(\d+)\.jpg", name)
    C = Mz @ (s * (R @ (-Rc.T @ tv)) + t); Rw = Mz @ (R @ Rc.T)
    cams.append({"name": name, "clip": m.group(1), "t": round(int(m.group(2)) / 59.94, 3), "pos": np.round(C, 3).tolist(), "quat": R_to_q(Rw)})
cams.sort(key=lambda c: (c["clip"], c["t"]))
gates = {"A": [3.6, 0, 15.2], "B": [45.0, 0, -4.0], "C": [43.6, 0, -43.0], "D": [3.3, 0, -33.9]}   # web frame: z = -north
json.dump({"frame": "web: x east, y up, z south, metres; quat (x,y,z,w) world-from-camera, COLMAP camera axes", "cameras": cams, "gates": gates,
           "video": {"fps": 59.94, "clips": {"a": "DJI_20260919071016_0016_D.MP4", "b": "DJI_20260919071517_0017_D.MP4"}}}, open(sys.argv[3], "w"))
print(len(cams), "scan cameras")
