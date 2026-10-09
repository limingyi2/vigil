# -*- coding: utf-8 -*-
"""Push client code5 (model page rewrite) to the cloud update channel."""
import hashlib
import json

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
LOCAL_EXE = r"F:\Vigil\vigil-client\dist_new7\Vigil Setup 0.1.0.exe"
NOTES = ("模型页重做：直达云端大脑（默认），换服务商自动带地址+自动拉模型清单，"
         "密钥按服务商认领（切回老家免重填）；换不了模型的几处根因全修")


def main():
    data = open(LOCAL_EXE, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    size = len(data)
    print("exe: sha256=%s... size=%d" % (sha[:16], size))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()
    sftp.put(LOCAL_EXE, "C:/Vigil/app/client/VigilSetup.exe")
    vf = {"code": 5, "ver": "0.1.0", "notes": NOTES, "sha256": sha, "size": size}
    with sftp.open("C:/Vigil/app/client/client_version.json", "w") as f:
        f.write(json.dumps(vf, ensure_ascii=False, indent=2))
    st = sftp.stat("C:/Vigil/app/client/VigilSetup.exe")
    print("cloud exe size:", st.st_size, "match:", st.st_size == size)
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


if __name__ == "__main__":
    main()
