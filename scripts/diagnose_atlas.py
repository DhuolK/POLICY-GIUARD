"""Layer-by-layer MongoDB Atlas connectivity diagnostic for the cPanel server.

Run ON THE SERVER, inside the app's virtualenv (adjust the version folder to
what cPanel -> Setup Python App shows):

    cd ~/policyguard
    ~/virtualenv/policyguard/3.11/bin/python scripts/diagnose_atlas.py

Why: "ServerSelectionTimeoutError: No replica set members found yet" only says
the driver reached NO shard. It never says WHICH layer failed, and each layer
has a different fix:

  LAYER 1  venv deps     pymongo + dnspython installed? (mongodb+srv needs
                         dnspython or the URI cannot even be resolved)
  LAYER 2  config        MONGO_URI present in .env / environment?
  LAYER 3  HTTPS egress  can the server reach the internet on 443 at all?
                         Also prints the server's PUBLIC egress IP — the
                         address Atlas Network Access must whitelist.
  LAYER 4  DNS           do the Atlas hostnames resolve? ("Name or service
                         not known" in stderr = this layer is broken)
  LAYER 5  TCP :27017    can a raw socket connect? A silent TIMEOUT here
                         (not "refused") = egress firewall dropping packets;
                         very common on shared cPanel hosting.
  LAYER 6  TLS           does the Atlas handshake complete? TCP+TLS ok but
                         ping fails = server IP NOT in Atlas Network Access.
  LAYER 7  pymongo ping  full end-to-end auth + round trip.

The first FAIL is the layer to fix; everything below it is expected to fail.
Non-destructive: DNS lookups, TCP connects, TLS handshakes, one ping.
Nothing sensitive is printed — the MONGO_URI password is masked.
"""

import os
import re
import socket
import ssl
import sys
import time

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = 'PASS'
FAIL = 'FAIL'

results = []  # (layer, status, detail)


def report(layer, status, detail):
    results.append((layer, status, detail))
    print(f'  [{status}] {detail}')


def mask_uri(uri):
    """mongodb[+srv]://user:password@host/... -> user:****@host/..."""
    return re.sub(r'(://[^:/@\s]+:)[^@\s]+@', r'\1****@', uri)


def load_env_value(key):
    """Read key from process env first, then .env in the app root.

    Deliberately dependency-free: this script must run even in a
    half-installed virtualenv (that is often the thing being diagnosed).
    """
    if os.environ.get(key):
        return os.environ[key]
    env_path = os.path.join(APP_ROOT, '.env')
    try:
        with open(env_path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def parse_hosts(uri):
    """Return (is_srv, [(host, port_or_None), ...]) without touching DNS."""
    m = re.match(r'mongodb(\+srv)?://(?:[^@/\s]+@)?([^/?\s]+)', uri or '')
    if not m:
        return None, []
    is_srv = bool(m.group(1))
    hosts = []
    for item in m.group(2).split(','):
        item = item.strip()
        if not item:
            continue
        if ':' in item:
            host, _, port = item.rpartition(':')
            try:
                hosts.append((host, int(port)))
            except ValueError:
                hosts.append((item, None))
        else:
            hosts.append((item, None if is_srv else 27017))
    return is_srv, hosts


def timed(fn):
    start = time.monotonic()
    out = fn()
    return out, (time.monotonic() - start) * 1000.0


def main():
    print('=' * 70)
    print('PolicyGuard - Atlas connectivity diagnostic')
    print('=' * 70)

    # ── LAYER 1: venv deps ───────────────────────────────────────────
    print('\nLAYER 1  Python / driver versions')
    print(f'  python {sys.version.split()[0]}  ({sys.executable})')
    try:
        import pymongo
        report('deps', PASS, f'pymongo {pymongo.version}')
    except ImportError:
        report('deps', FAIL, 'pymongo NOT installed in this virtualenv - '
                             'run: pip install -r requirements.txt')
        return verdict()
    try:
        import dns.resolver  # noqa: F401
        report('deps', PASS, 'dnspython installed (mongodb+srv can resolve)')
        have_dns = True
    except ImportError:
        report('deps', FAIL, 'dnspython NOT installed - mongodb+srv:// URIs '
                             'cannot resolve. run: pip install -r requirements.txt')
        have_dns = False

    # ── LAYER 2: config ──────────────────────────────────────────────
    print('\nLAYER 2  MONGO_URI configuration')
    uri = load_env_value('MONGO_URI')
    if not uri:
        report('config', FAIL, 'MONGO_URI not found in environment or '
                               f'{APP_ROOT}/.env')
        return verdict()
    report('config', PASS, f'MONGO_URI = {mask_uri(uri)}')
    db_name = load_env_value('MONGO_DB_NAME') or '(unset - app defaults to policy_guard)'
    print(f'        MONGO_DB_NAME = {db_name}')

    is_srv, hosts = parse_hosts(uri)
    if not hosts:
        report('config', FAIL, 'could not parse any host out of MONGO_URI - check for typos')
        return verdict()
    scheme = 'mongodb+srv' if is_srv else 'mongodb'
    report('config', PASS, f'scheme {scheme}://, seed host(s): '
                           + ', '.join(h for h, _ in hosts))

    # ── LAYER 3: HTTPS egress + public IP ────────────────────────────
    print('\nLAYER 3  Outbound HTTPS (port 443) + public egress IP')
    try:
        import urllib.request
        with urllib.request.urlopen('https://api.ipify.org', timeout=8) as resp:
            egress_ip = resp.read().decode().strip()
        report('egress-443', PASS,
               f'HTTPS egress works. Server public IP = {egress_ip}\n'
               '        -> this IP (or 0.0.0.0/0 for a quick test) MUST appear in\n'
               '          Atlas -> Network Access -> IP Access List.')
    except Exception as exc:
        report('egress-443', FAIL, f'cannot reach https://api.ipify.org: {exc}\n'
               "        If even 443 is blocked, Daraja/Africa's Talking will "
               'fail too - talk to the host.')

    # ── LAYER 4: DNS ─────────────────────────────────────────────────
    print('\nLAYER 4  DNS resolution')
    targets = list(hosts)
    if is_srv:
        seed = hosts[0][0]
        if not have_dns:
            report('dns-srv', FAIL, 'cannot do the SRV lookup without dnspython (layer 1)')
            return verdict()
        try:
            import dns.resolver
            answers = dns.resolver.resolve(f'_mongodb._tcp.{seed}', 'SRV', lifetime=6)
            targets = [(str(r.target).rstrip('.'), int(r.port)) for r in answers]
            report('dns-srv', PASS, f'SRV _mongodb._tcp.{seed} -> '
                                    + ', '.join(f'{h}:{p}' for h, p in targets))
        except Exception as exc:
            report('dns-srv', FAIL, f'SRV lookup for _mongodb._tcp.{seed} failed: {exc}\n'
                   '        This is the "Name or service not known" layer - the\n'
                   "        host's resolver is broken/blocked. Ask the host, or\n"
                   '        switch to a standard mongodb:// URI listing the three\n'
                   '        shard hosts directly (Atlas -> Connect -> Drivers).')
            return verdict()

    dns_ok = True
    for host, _port in targets:
        try:
            infos, ms = timed(lambda: socket.getaddrinfo(host, None))
            ips = sorted({i[4][0] for i in infos})
            report('dns-a', PASS, f'{host} -> {", ".join(ips)}  ({ms:.0f} ms)')
        except socket.gaierror as exc:
            dns_ok = False
            report('dns-a', FAIL, f'{host}: {exc}  (DNS cannot resolve the shard hostname)')
    if not dns_ok:
        return verdict()


    # ── LAYER 5: raw TCP connect to :27017 ───────────────────────────
    print('\nLAYER 5  Raw TCP connect to port 27017')
    tcp_ok_hosts = []
    for host, port in targets:
        port = port or 27017
        try:
            _sock, ms = timed(lambda: socket.create_connection((host, port), timeout=6))
            _sock.close()
            tcp_ok_hosts.append((host, port))
            report('tcp', PASS, f'{host}:{port} connected  ({ms:.0f} ms)')
        except socket.timeout:
            report('tcp', FAIL, f'{host}:{port} TIMED OUT - packets are being silently '
                   'dropped. Classic shared-hosting egress block on port 27017.')
        except OSError as exc:
            report('tcp', FAIL, f'{host}:{port} {exc}')
    if not tcp_ok_hosts:
        print('\n  No shard accepted a TCP connection.')
        return verdict()

    # ── LAYER 6: TLS handshake ───────────────────────────────────────
    print('\nLAYER 6  TLS handshake')
    tls_ok = True
    for host, port in tcp_ok_hosts:
        try:
            def _tls():
                ctx = ssl.create_default_context()
                raw = socket.create_connection((host, port), timeout=6)
                return ctx.wrap_socket(raw, server_hostname=host)
            ssock, ms = timed(_tls)
            cert = ssock.getpeercert()
            subject = dict(x[0] for x in cert.get('subject', ())).get('commonName', '?')
            issuer = dict(x[0] for x in cert.get('issuer', ())).get('organizationName', '?')
            ssock.close()
            report('tls', PASS, f'{host}:{port} TLS OK  ({ms:.0f} ms) '
                                f'cert CN={subject} issued by {issuer}')
        except Exception as exc:
            tls_ok = False
            report('tls', FAIL, f'{host}:{port} TLS failed: {exc}')
    if not tls_ok:
        return verdict()

    # ── LAYER 7: real pymongo ping ───────────────────────────────────
    print('\nLAYER 7  pymongo end-to-end ping (real MONGO_URI)')
    try:
        import pymongo
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=8000)
        _pong, ms = timed(lambda: client.admin.command('ping'))
        report('pymongo', PASS, f'ping ok  ({ms:.0f} ms) - the database IS reachable '
               'from this server with these credentials.')
        client.close()
    except Exception as exc:
        report('pymongo', FAIL, f'{type(exc).__name__}: {exc}')

    return verdict()


def verdict():
    print('\n' + '=' * 70)
    print('VERDICT')
    print('=' * 70)
    failing = [r for r in results if r[1] == FAIL]
    if not failing:
        print('  All layers PASS from this server.')
        print('  If the web app still throws ServerSelectionTimeoutError, the')
        print('  Passenger worker is seeing a DIFFERENT config than this shell:')
        print('  check the MONGO_URI env var set in cPanel -> Setup Python App,')
        print('  then click Restart (or touch tmp/restart.txt) and retest.')
        return 0

    first = failing[0][0]
    advice = {
        'deps': 'Install everything into the app virtualenv:\n'
                '    cd ~/policyguard && ~/virtualenv/policyguard/<pyver>/bin/pip '
                'install -r requirements.txt',
        'config': 'Fix MONGO_URI in ~/policyguard/.env (or the cPanel env vars).',
        'egress-443': 'The host blocks outbound HTTPS too - open a ticket; nothing '
                      "external (Atlas, Daraja, Africa's Talking) will work until fixed.",
        'dns-srv': "DNS SRV lookups fail on this host. Ask the host to fix the resolver, "
                   'or use a standard mongodb:// URI listing the three shard hosts.',
        'dns-a': "The host's DNS cannot resolve Atlas shard hostnames - ask the host "
                 'to fix DNS resolution for *.mongodb.net.',
        'tcp': 'Outbound TCP 27017 is blocked or the route is dead.\n'
               '    1) First make sure Atlas -> Network Access contains this '
               "server's public IP (from LAYER 3) - add 0.0.0.0/0 temporarily and retest.\n"
               '    2) If it STILL times out, open a Truehost ticket: "Please allow '
               'outbound TCP port 27017 to *.mongodb.net for my account."\n'
               '    3) If they refuse (common on shared hosting), the realistic options '
               'are a VPS or a platform that allows Atlas egress (Render, Railway, '
               'PythonAnywhere).',
        'tls': 'TCP connects but TLS is intercepted/broken - unusual; paste this '
               'output into a ticket to the host.',
        'pymongo': "Network path is fine but Mongo failed the handshake.\n"
               "    Most likely: this server's egress IP is NOT in Atlas -> Network "
               'Access (add the IP from LAYER 3, or 0.0.0.0/0 to test).\n'
               '    Otherwise: wrong username/password in MONGO_URI, or the DB user '
               'lacks privileges (Atlas -> Database Access).',
    }
    print(f'  First failing layer: {first}\n')
    for line in advice.get(first, 'See the failing layer above.').splitlines():
        print(f'  {line}')
    return 1


if __name__ == '__main__':
    sys.exit(main() or 0)

