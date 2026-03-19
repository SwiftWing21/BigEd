"""
Local network penetration testing skill — authorized scan of own network only.
Discovers hosts, scans services, checks for common misconfigs, and generates
a findings report that feeds into the security advisory workflow.

Requires: nmap (sudo apt install nmap)
"""
import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
PENTEST_DIR = KNOWLEDGE_DIR / "security" / "pen_tests"
PENDING_DIR = KNOWLEDGE_DIR / "security" / "pending"
REQUIRES_NETWORK = True


def _ollama(prompt, config):
    resp = httpx.post(
        f"{config['models']['ollama_host']}/api/generate",
        json={"model": config["models"]["local"], "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _get_local_network():
    """Detect local network range from ip route."""
    try:
        out = subprocess.run(
            ["ip", "route"], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines():
            # e.g. "192.168.1.0/24 dev eth0 ..."
            parts = line.split()
            if parts and "/" in parts[0] and not parts[0].startswith("default"):
                return parts[0]
    except Exception:
        pass
    return "192.168.1.0/24"  # fallback


def _run_nmap(target, scan_type="quick"):
    """Run nmap and return XML output parsed into host list."""
    if scan_type == "quick":
        # Fast scan: top 100 ports, OS detection off, no scripts
        args = ["-T4", "--top-ports", "100", "-oX", "-"]
    elif scan_type == "service":
        # Service/version detection on common ports
        args = ["-sV", "--version-intensity", "5", "-p",
                "21,22,23,25,53,80,110,143,443,445,3306,3389,5432,6379,8080,8443,9200,11434",
                "-oX", "-"]
    elif scan_type == "full":
        # Full port scan — slower
        args = ["-sV", "-p-", "-T4", "-oX", "-"]
    else:
        args = ["-T4", "--top-ports", "100", "-oX", "-"]

    try:
        result = subprocess.run(
            ["nmap"] + args + [target],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode not in (0, 1):
            return None, f"nmap error: {result.stderr[:200]}"
        return result.stdout, None
    except FileNotFoundError:
        return None, "nmap not installed — run: sudo apt install nmap"
    except subprocess.TimeoutExpired:
        return None, "nmap scan timed out"


def _parse_nmap_xml(xml_str):
    """Parse nmap XML output into structured host data."""
    hosts = []
    try:
        root = ET.fromstring(xml_str)
        for host_el in root.findall("host"):
            status = host_el.find("status")
            if status is None or status.get("state") != "up":
                continue

            addr_el = host_el.find("address")
            ip = addr_el.get("addr", "?") if addr_el is not None else "?"

            hostname_els = host_el.findall("hostnames/hostname")
            hostname = hostname_els[0].get("name", "") if hostname_els else ""

            ports = []
            for port_el in host_el.findall("ports/port"):
                state_el = port_el.find("state")
                if state_el is None or state_el.get("state") != "open":
                    continue
                svc_el = port_el.find("service")
                ports.append({
                    "port": port_el.get("portid"),
                    "protocol": port_el.get("protocol"),
                    "service": svc_el.get("name", "") if svc_el is not None else "",
                    "product": svc_el.get("product", "") if svc_el is not None else "",
                    "version": svc_el.get("version", "") if svc_el is not None else "",
                })

            hosts.append({"ip": ip, "hostname": hostname, "open_ports": ports})
    except ET.ParseError:
        pass
    return hosts


def _assess_findings(hosts):
    """Generate security findings from nmap host data."""
    findings = []

    HIGH_RISK_PORTS = {
        "23": ("Telnet open", "HIGH", "Plaintext protocol — disable and use SSH"),
        "21": ("FTP open", "MEDIUM", "Plaintext protocol — use SFTP/SCP instead"),
        "3389": ("RDP exposed", "HIGH", "Remote Desktop exposed — restrict with firewall or VPN"),
        "445": ("SMB exposed", "HIGH", "SMB port open — check for EternalBlue, restrict to LAN"),
        "3306": ("MySQL exposed", "HIGH", "Database port exposed — bind to localhost only"),
        "5432": ("PostgreSQL exposed", "HIGH", "Database port exposed — bind to localhost only"),
        "6379": ("Redis exposed", "HIGH", "Redis often has no auth — bind to localhost only"),
        "9200": ("Elasticsearch exposed", "HIGH", "Elasticsearch may have no auth — restrict access"),
        "27017": ("MongoDB exposed", "HIGH", "MongoDB may have no auth — restrict access"),
        "11434": ("Ollama API exposed", "MEDIUM", "Ollama API accessible on LAN — ensure firewall blocks WAN access"),
        # UniFi-specific
        "27117": ("UniFi embedded MongoDB exposed", "HIGH", "UniFi internal MongoDB — must not be externally accessible"),
        "8880": ("UniFi guest portal HTTP", "MEDIUM", "Guest portal unencrypted — verify intended exposure"),
    }

    INTERESTING_PORTS = {
        "80": "HTTP (unencrypted web)",
        "443": "HTTPS",
        "22": "SSH",
        # UniFi controller + infrastructure
        "8080": "UniFi controller HTTP / HTTP alternate",
        "8443": "UniFi controller HTTPS admin UI",
        "8843": "UniFi guest portal HTTPS",
        "3478": "UniFi STUN / device adoption (UDP)",
        "10001": "UniFi device discovery (UDP)",
        "6789": "UniFi throughput test",
    }

    # UniFi-specific advisory context
    UNIFI_PORTS = {"8080", "8443", "8843", "8880", "3478", "10001", "6789", "27117"}

    for host in hosts:
        for port_info in host["open_ports"]:
            port = port_info["port"]
            svc = port_info.get("service", "")
            product = port_info.get("product", "")
            version = port_info.get("version", "")

            if port in HIGH_RISK_PORTS:
                label, severity, fix = HIGH_RISK_PORTS[port]
                findings.append({
                    "severity": severity,
                    "type": "network_exposure",
                    "host": host["ip"],
                    "hostname": host["hostname"],
                    "port": port,
                    "service": svc,
                    "detail": f"{label} on {host['ip']}:{port} ({f'{product} {version}'.strip()})",
                    "fix": fix,
                })

            elif port in INTERESTING_PORTS and (product or svc):
                version_str = f"{product} {version}".strip()
                findings.append({
                    "severity": "INFO",
                    "type": "service_inventory",
                    "host": host["ip"],
                    "hostname": host["hostname"],
                    "port": port,
                    "service": svc,
                    "detail": f"{INTERESTING_PORTS[port]}: {version_str} on {host['ip']}:{port}",
                    "fix": "No immediate action — review version for known CVEs",
                })

        # Flag hosts with many open ports (potential misconfiguration)
        if len(host["open_ports"]) > 15:
            findings.append({
                "severity": "MEDIUM",
                "type": "attack_surface",
                "host": host["ip"],
                "detail": f"{host['ip']} has {len(host['open_ports'])} open ports — review for unnecessary services",
                "fix": "Audit running services and disable unused ones",
            })

    return findings


def _check_localhost_binding():
    """Verify local services (Ollama, Dashboard) are bound to 127.0.0.1 only."""
    findings = []
    # Ports to check: Ollama (11434), Dashboard (5555)
    ports_to_check = [
        (11434, "Ollama API"),
        (5555, "Fleet Dashboard"),
    ]
    try:
        # Try ss (Linux) first, then netstat
        for cmd in [["ss", "-tlnp"], ["netstat", "-tlnp"]]:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    lines = result.stdout
                    for port, name in ports_to_check:
                        port_str = f":{port}"
                        for line in lines.splitlines():
                            if port_str in line:
                                # Check if bound to 0.0.0.0 or :: (all interfaces)
                                if f"0.0.0.0:{port}" in line or f":::{port}" in line or f"*:{port}" in line:
                                    findings.append({
                                        "severity": "HIGH",
                                        "type": "network_binding",
                                        "port": port,
                                        "detail": f"{name} (port {port}) bound to all interfaces (0.0.0.0) — accessible from LAN",
                                        "fix": f"Bind {name} to 127.0.0.1 only (--host 127.0.0.1)",
                                    })
                                elif f"127.0.0.1:{port}" in line:
                                    findings.append({
                                        "severity": "INFO",
                                        "type": "network_binding",
                                        "port": port,
                                        "detail": f"{name} (port {port}) correctly bound to 127.0.0.1",
                                        "fix": "No action needed",
                                    })
                    break  # found a working command
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    except Exception:
        pass
    return findings


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    target = payload.get("target", "auto")
    scan_type = payload.get("scan_type", "service")  # quick | service | full
    label = payload.get("label", "local_network")
    include_hardening = config.get("security", {}).get("network_hardening_enabled", True)

    if target == "auto":
        target = _get_local_network()

    scan_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    PENTEST_DIR.mkdir(parents=True, exist_ok=True)

    # Run nmap
    xml_output, nmap_error = _run_nmap(target, scan_type)
    if nmap_error:
        return {"error": nmap_error, "target": target}

    # Save raw XML
    raw_file = PENTEST_DIR / f"scan_{scan_id}_{label}.xml"
    raw_file.write_text(xml_output)

    # Parse results
    hosts = _parse_nmap_xml(xml_output)
    findings = _assess_findings(hosts)

    # Network hardening: verify localhost-only binding
    if include_hardening:
        findings.extend(_check_localhost_binding())

    # Build summary for Ollama analysis
    host_summary = "\n".join(
        f"  {h['ip']} ({h['hostname']}): {len(h['open_ports'])} open ports — "
        + ", ".join(f"{p['port']}/{p['service']}" for p in h["open_ports"][:8])
        for h in hosts
    )
    findings_summary = "\n".join(
        f"  [{f['severity']}] {f['detail']}"
        for f in findings if f["severity"] in ("HIGH", "MEDIUM")
    ) or "  No high/medium findings."

    # Check if any UniFi ports were seen
    unifi_hosts = [
        h for h in hosts
        if any(p["port"] in UNIFI_PORTS for p in h["open_ports"])
    ]
    unifi_note = ""
    if unifi_hosts:
        unifi_note = (
            f"\nUniFi infrastructure detected on: "
            + ", ".join(h["ip"] for h in unifi_hosts)
            + "\nApply UniFi-specific hardening: disable default SSH credentials, "
            "enable 2FA on controller, restrict controller port 8443 to management VLAN, "
            "keep firmware current, disable UPnP, review guest VLAN isolation."
        )

    prompt = f"""You are a security advisor reviewing a local network penetration test.
The network includes Ubiquiti UniFi infrastructure (routers, APs, switches).

Target: {target}
Scan type: {scan_type}
Hosts discovered: {len(hosts)}
{unifi_note}

Host inventory:
{host_summary or '  No hosts found.'}

Key findings:
{findings_summary}

Write a concise post-pentest security report (max 8 bullet points):
- Lead with HIGH severity findings and exact remediation steps
- Call out any UniFi controller/device exposure or misconfiguration
- Note services that should not be LAN-exposed (databases, Ollama API, UniFi MongoDB)
- Include 2-3 hardening recommendations for a home lab running local AI services on UniFi
- End with a one-line overall risk rating: LOW / MEDIUM / HIGH"""

    analysis = _ollama(prompt, config)

    # Build full report
    report = {
        "scan_id": scan_id,
        "created_at": datetime.now().isoformat(),
        "target": target,
        "scan_type": scan_type,
        "hosts_discovered": len(hosts),
        "hosts": hosts,
        "findings": findings,
        "analysis": analysis,
        "raw_xml": str(raw_file),
    }

    report_file = PENTEST_DIR / f"pentest_{scan_id}_{label}.json"
    report_file.write_text(json.dumps(report, indent=2))

    # Build markdown report
    high = [f for f in findings if f["severity"] == "HIGH"]
    medium = [f for f in findings if f["severity"] == "MEDIUM"]
    info = [f for f in findings if f["severity"] == "INFO"]

    md_lines = [
        f"# Pen Test Report — {target}",
        f"**Scan ID:** {scan_id}  **Type:** {scan_type}  **Hosts:** {len(hosts)}",
        f"**Findings:** {len(high)} HIGH, {len(medium)} MEDIUM, {len(info)} INFO",
        "",
        "## Security Analysis",
        analysis,
        "",
        "## Host Inventory",
    ]
    for h in hosts:
        ports_str = ", ".join(f"{p['port']}/{p['service']}" for p in h["open_ports"])
        md_lines.append(f"- **{h['ip']}** ({h['hostname'] or 'no hostname'}): {ports_str or 'no open ports'}")

    if high or medium:
        md_lines += ["", "## Findings Requiring Action"]
        for f in sorted(findings, key=lambda x: {"HIGH": 0, "MEDIUM": 1}.get(x["severity"], 2)):
            if f["severity"] in ("HIGH", "MEDIUM"):
                md_lines.append(f"- **[{f['severity']}]** {f['detail']}")
                if f.get("fix"):
                    md_lines.append(f"  - Remediation: {f['fix']}")

    md_lines += ["", f"*Raw nmap XML: {raw_file}*"]

    md_file = PENTEST_DIR / f"pentest_{scan_id}_{label}.md"
    md_file.write_text("\n".join(md_lines))

    # If HIGH findings, create a pending security advisory
    if high:
        import hashlib
        advisory_id = hashlib.sha1(f"pentest_{scan_id}".encode()).hexdigest()[:8]
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        advisory = {
            "id": advisory_id,
            "created_at": datetime.now().isoformat(),
            "scope": f"pen_test_{target}",
            "status": "PENDING_APPROVAL",
            "source": "pen_test",
            "counts": {"HIGH": len(high), "MEDIUM": len(medium), "LOW": 0},
            "findings": high + medium,
            "analysis": analysis,
        }
        adv_file = PENDING_DIR / f"advisory_{advisory_id}.json"
        adv_file.write_text(json.dumps(advisory, indent=2))

        adv_md = PENDING_DIR / f"advisory_{advisory_id}.md"
        adv_md.write_text("\n".join([
            f"# Security Advisory {advisory_id} (from pen test)",
            f"**Target:** {target}  **Scan:** {scan_id}",
            "",
            "## Analysis",
            analysis,
            "",
            "## HIGH Findings",
        ] + [f"- {f['detail']} — Fix: {f.get('fix', 'manual review')}" for f in high] + [
            "",
            "## To Apply Automated Fixes",
            f"```",
            f"uv run python lead_client.py task 'security_apply {advisory_id}' --wait",
            f"```",
        ]))

        db.post_message(
            from_agent="security",
            to_agent="lead",
            body_json=json.dumps({
                "type": "pentest_advisory",
                "advisory_id": advisory_id,
                "target": target,
                "counts": {"HIGH": len(high), "MEDIUM": len(medium)},
                "summary": analysis[:500],
                "report_file": str(md_file),
            })
        )

    # Always notify lead of completed scan
    db.post_message(
        from_agent="security",
        to_agent="lead",
        body_json=json.dumps({
            "type": "pentest_complete",
            "scan_id": scan_id,
            "target": target,
            "hosts_found": len(hosts),
            "high_findings": len(high),
            "report_file": str(md_file),
        })
    )

    return {
        "scan_id": scan_id,
        "target": target,
        "hosts_discovered": len(hosts),
        "findings": {"HIGH": len(high), "MEDIUM": len(medium), "INFO": len(info)},
        "report_file": str(md_file),
        "advisory_created": len(high) > 0,
    }
