"""Generate a private, short-lived example CA and a loopback server/client pair."""

import argparse
import os
import subprocess
from pathlib import Path


def generate(output, second_client=False):
    output = Path(output)
    output.mkdir(mode=0o700, parents=True)

    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=output, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)

    mask = os.umask(0o077)
    try:
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj", "/CN=A2A test CA",
                "-keyout", "ca.key", "-out", "ca.crt")
        certificates = [("server", "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n"),
                        ("client", "extendedKeyUsage=clientAuth\n")]
        if second_client:
            certificates.append(("client2", "extendedKeyUsage=clientAuth\n"))
        for name, usages in certificates:
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
                    "-keyout", f"{name}.key", "-out", f"{name}.csr")
            (output / f"{name}.ext").write_text(usages, encoding="ascii")
            openssl("x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
                    "-days", "30", "-extfile", f"{name}.ext", "-out", f"{name}.crt")
    finally:
        os.umask(mask)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="secrets")
    parser.add_argument("--second-client", action="store_true")
    args = parser.parse_args()
    generate(args.output, args.second_client)
