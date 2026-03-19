"""
Legal document drafting skill — generates business legal document drafts using the local model.

IMPORTANT: Output is a DRAFT for attorney review — not legal advice.
Saves to knowledge/legal/<doc_type>_<client>_<date>.md

Supported document types:
  nda               Non-Disclosure Agreement (mutual or one-way)
  service_agreement Client Service Agreement for AI implementation
  sow               Statement of Work
  dpa               Data Processing Agreement (for healthcare/regulated clients)
  privacy_policy    Privacy Policy for a customer fleet deployment
  tos               Terms of Service
  consultant        Independent Contractor / Consultant Agreement
  proposal          Business Proposal / Engagement Letter

Payload:
  doc_type    str   one of the types above
  client_name str   client or party name
  our_name    str   our company name (default: "BigEd AI Services")
  context     str   optional — extra details (scope, special terms, jurisdiction)
  jurisdiction str  optional — e.g. "California" (default: "California")
"""
import os
from datetime import datetime
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "legal_draft"
DESCRIPTION = "Legal document drafting skill — generates business legal document drafts using t"

FLEET_DIR   = Path(__file__).parent.parent
LEGAL_DIR   = FLEET_DIR / "knowledge" / "legal"

_OUR_DEFAULT = "BigEd AI Services"
_JX_DEFAULT  = "California"

# Each doc type gets a focused prompt template
_DOC_SPECS = {
    "nda": {
        "title": "Non-Disclosure Agreement",
        "guidance": (
            "Mutual NDA. Cover: definition of confidential information, exclusions, "
            "obligations of both parties, term (2 years), return/destruction of materials, "
            "governing law, no license grant. Keep plain language — avoid legalese where possible."
        ),
    },
    "service_agreement": {
        "title": "Client Service Agreement",
        "guidance": (
            "Agreement for local AI implementation services. Cover: scope of services (local AI fleet deployment, "
            "no cloud data transfer, on-premise hardware), fees and payment terms, IP ownership (client owns their data, "
            "we retain our tools/methods), confidentiality, limitation of liability, term and termination, "
            "warranty disclaimer (AI outputs require human review). California governing law."
        ),
    },
    "sow": {
        "title": "Statement of Work",
        "guidance": (
            "Project-specific SOW. Cover: project description, deliverables list, timeline/milestones, "
            "fees and payment schedule, acceptance criteria, change order process, "
            "client responsibilities (hardware access, IT support). "
            "Reference the master Service Agreement for legal terms."
        ),
    },
    "dpa": {
        "title": "Data Processing Agreement",
        "guidance": (
            "DPA for regulated industries (healthcare, legal, accounting). Cover: "
            "data controller vs processor roles, types of personal data processed, "
            "processing purpose and legal basis, technical safeguards (local processing only, no cloud), "
            "sub-processor restrictions, breach notification (72 hours), "
            "data subject rights, audit rights, HIPAA Business Associate clauses if healthcare, "
            "CCPA compliance for California clients."
        ),
    },
    "privacy_policy": {
        "title": "Privacy Policy",
        "guidance": (
            "Privacy policy for a local AI fleet deployment. Cover: "
            "what data is collected (usage logs, task history — all local), "
            "what is NOT collected (no data sent to cloud without explicit consent), "
            "how data is stored and secured (encrypted local storage), "
            "data retention period, user rights (access, deletion), "
            "contact information. Plain language, CCPA compliant."
        ),
    },
    "tos": {
        "title": "Terms of Service",
        "guidance": (
            "ToS for AI implementation and managed fleet services. Cover: "
            "acceptable use (no illegal activity, no unauthorized access), "
            "service availability (best effort, scheduled maintenance), "
            "AI output disclaimer (requires human review, not professional advice), "
            "payment terms, subscription tiers, cancellation policy, "
            "limitation of liability, dispute resolution (arbitration, California)."
        ),
    },
    "consultant": {
        "title": "Independent Contractor Agreement",
        "guidance": (
            "Agreement for hiring a contractor/consultant. Cover: "
            "independent contractor status (not employee), scope of work, "
            "compensation and invoicing, IP assignment (work-for-hire), "
            "confidentiality, non-solicitation (12 months), "
            "term and termination for convenience, tax responsibility (1099)."
        ),
    },
    "proposal": {
        "title": "Business Proposal / Engagement Letter",
        "guidance": (
            "Professional proposal for AI implementation services. Cover: "
            "executive summary of the opportunity, proposed solution (local AI fleet), "
            "key benefits (privacy, cost reduction, efficiency), "
            "implementation timeline, investment summary (one-time setup + monthly retainer), "
            "next steps and call to action. Tone: professional, confident, concise."
        ),
    },
}



def run(payload, config):
    doc_type     = payload.get("doc_type", "nda").lower()
    client_name  = payload.get("client_name", "Client")
    our_name     = payload.get("our_name", _OUR_DEFAULT)
    context      = payload.get("context", "")
    jurisdiction = payload.get("jurisdiction", _JX_DEFAULT)

    if doc_type not in _DOC_SPECS:
        return {
            "error": f"Unknown doc_type '{doc_type}'",
            "available": list(_DOC_SPECS.keys()),
        }

    spec = _DOC_SPECS[doc_type]

    system = "You are a paralegal drafting business legal documents. Write a professional, complete draft."

    user = f"""DOCUMENT TYPE: {spec['title']}
PARTY 1 (Service Provider): {our_name}
PARTY 2 (Client / Other Party): {client_name}
GOVERNING LAW: {jurisdiction}
{f"ADDITIONAL CONTEXT: {context}" if context else ""}

DRAFTING GUIDANCE:
{spec['guidance']}

Format the document with:
- Document title and date
- Recitals / whereas clauses
- Numbered sections with clear headings
- Signature block for both parties

Begin with this disclaimer on the first line:
DRAFT — FOR ATTORNEY REVIEW ONLY — NOT LEGAL ADVICE

Write the complete document now:"""

    draft = call_complex(system, user, config)

    LEGAL_DIR.mkdir(parents=True, exist_ok=True)
    safe_client = client_name.lower().replace(" ", "_")[:20]
    date_str    = datetime.now().strftime("%Y%m%d")
    out_file    = LEGAL_DIR / f"{doc_type}_{safe_client}_{date_str}.md"

    header = (
        f"# {spec['title']}\n"
        f"**Parties:** {our_name} ↔ {client_name}\n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Jurisdiction:** {jurisdiction}\n\n"
        f"> ⚠️  DRAFT — FOR ATTORNEY REVIEW ONLY — NOT LEGAL ADVICE\n\n---\n\n"
    )
    out_file.write_text(header + draft)

    return {
        "doc_type":   doc_type,
        "title":      spec["title"],
        "client":     client_name,
        "saved_to":   str(out_file),
        "preview":    draft[:300],
    }