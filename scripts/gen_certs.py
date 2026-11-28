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
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=A2A test CA",
                "-keyout", "ca.key", "-out", "ca.csr")
        (output / "ca.ext").write_text(
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\n", encoding="ascii")
        openssl("x509", "-req", "-in", "ca.csr", "-signkey", "ca.key", "-days", "30",
                "-extfile", "ca.ext", "-out", "ca.crt")
        leaf_extensions = ("basicConstraints=CA:FALSE\nsubjectKeyIdentifier=hash\n"
                           "authorityKeyIdentifier=keyid,issuer\n")
        client_usages = "keyUsage=digitalSignature\nextendedKeyUsage=clientAuth\n"
        certificates = [("server", "keyUsage=digitalSignature,keyEncipherment\n"
                                   "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n"),
                        ("client", client_usages)]
        if second_client:
            certificates.append(("client2", client_usages))
        for name, usages in certificates:
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
                    "-keyout", f"{name}.key", "-out", f"{name}.csr")
            (output / f"{name}.ext").write_text(leaf_extensions + usages, encoding="ascii")
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
