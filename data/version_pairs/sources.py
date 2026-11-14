"""Genuine RBI version chains: what to download, and from where.

**Data only.** The fetching logic is in ``fetch_version_pairs.py``; this file
is the list, kept separate because the URLs are long and a reviewer should be
able to read the provenance without reading the downloader. Same split as
``data/sample_corpus/sources.py``.

Why this corpus exists
----------------------
Compare had nothing real to run on. The 4-document development corpus and the
17-document Library & Search sample corpus both contain **zero version
pairs** -- every document has exactly one version -- so until now every claim
about clause alignment could only be demonstrated on synthetic fixtures. D-10
records this as the biggest blocker in the project.

These nine documents are three RBI **Master Circulars**, each in three
successive annual issues. A master circular is the ideal case for version
comparison and not a convenient one: the RBI reissues each one every year,
consolidating the instructions issued since the last issue, and **each issue
names its predecessor in its own opening paragraph**. For example the
2017 SC/ST circular begins:

    Please refer to the Master Circular FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17
    dated July 01, 2016 consolidating guidelines / Instructions / directions
    issued to banks with regard to providing credit facilities to Scheduled
    Castes (SCs) and Scheduled Tribes (STs). The Master Circular has been
    suitably updated by incorporating the instructions issued up to
    June 30, 2017...

So the version relationship is **asserted by the issuing authority in the
document text**, not inferred by us from title similarity. That is exactly the
standard ``CLAUDE.md`` D-9 sets for declaring one upload a version of another,
and it is what lets ``supersedes`` be filled in in the manifest honestly. The
quoted predecessor reference number for each row is recorded below as
``supersedes_evidence``, so a reader can open the PDF and check the claim.

Why these URLs and not ``rbidocs.rbi.org.in``
---------------------------------------------
``data/sample_corpus/sources.py`` records that ``rbidocs.rbi.org.in`` would not
complete a TLS handshake from this machine, which is why nine selected RBI
documents could not be downloaded for that corpus. The host now *connects*,
but it answers a PDF request with an F5/TSPD JavaScript bot-protection
challenge rather than the file -- a 200 response whose body is HTML, verified
on 2026-11-14. It is therefore still unusable from a script, for a different
reason than before.

``www.rbi.org.in`` serves the same master circulars as real PDFs under
``/commonman/Upload/English/Notification/PDFs/`` and has no such challenge.
Every URL below was found from that host's own master-circular index
(``index_page`` on each row), so the provenance chain is: RBI's index page ->
RBI's notification page -> RBI's PDF, all on the publisher's own domain.

What is human-asserted here, and what is not
--------------------------------------------
``title``, ``document_group``, ``version_label`` and ``supersedes`` are
**human-asserted** (``data/README.md`` §4). ``reference_no`` and
``publication_date`` were read off page 1 of each downloaded PDF and are
recorded here so the fetcher can cross-check them against the file it actually
received. ``sha256`` is never written in this file: it is computed from the
bytes that arrive.
"""

from __future__ import annotations

from typing import Dict, List

__all__ = ["CHAINS", "SAMPLES", "ALLOWED_HOSTS", "PDF_BASE"]

#: Only this host may be downloaded from. Checked before every request, so a
#: mirror or a blog cannot enter the corpus through a typo.
ALLOWED_HOSTS = frozenset({"www.rbi.org.in"})

PDF_BASE = "https://www.rbi.org.in/commonman/Upload/English/Notification/PDFs/"

#: Three logical documents, each with three successive issues, oldest first.
#: ``version_label`` is the issue date, which is how RBI itself identifies an
#: issue of a master circular, and ``supersedes`` is the ``version_label`` of
#: the issue the document's own text names as its predecessor.
CHAINS: List[Dict[str, object]] = [
    {
        "document_group": "rbi-master-circular-sc-st-credit-facilities",
        "title": (
            "Master Circular - Credit facilities to Scheduled Castes (SCs) & "
            "Scheduled Tribes (STs)"
        ),
        "department": "Financial Inclusion & Development Dept., Central Office",
        "document_type": "circular",
        "note": (
            "Same document_group as the 2026 issue already in "
            "data/manifest.csv, so the chain joins the existing corpus "
            "entry rather than creating a parallel document."
        ),
        "versions": [
            {
                "pdf": "5MC07072016.pdf",
                "version_label": "2016-07-01",
                "publication_date": "2016-07-01",
                "reference_no": "RBI/2016-17/05 FIDD.CO.GSSD.BC.No.03/09.09.01/2016-17",
                "supersedes": None,
                "supersedes_evidence": (
                    "Page 1: 'Please refer to the Master Circular "
                    "FIDD.CO.GSSD.BC.No 06/09.09.01/2015-16 dated July 01, "
                    "2015' -- the 2015 issue is not in this corpus, so this "
                    "row is the chain's base."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=1756",
                "expected_pages": 13,
            },
            {
                "pdf": "MC07EN03072017.pdf",
                "version_label": "2017-07-01",
                "publication_date": "2017-07-01",
                "reference_no": "RBI/2017-18/7 FIDD.CO.GSSD.BC.No.06/09.09.001/2017-18",
                "supersedes": "2016-07-01",
                "supersedes_evidence": (
                    "Page 1: 'Please refer to the Master Circular "
                    "FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17 dated July 01, "
                    "2016' -- which is the reference number of the row above."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=2316",
                "expected_pages": 8,
            },
            {
                "pdf": "6MC08F98715C50249D2AA3EC536CA889E64.pdf",
                "version_label": "2019-07-01",
                "publication_date": "2019-07-01",
                "reference_no": "RBI/2019-20/06 FIDD.CO.GSSD.BC.No.03/09.09.001/2019-20",
                "supersedes": "2017-07-01",
                "supersedes_evidence": (
                    "Page 1 refers to the preceding Master Circular in the "
                    "same series. The 2018 issue is not in this corpus, so "
                    "this link skips a year: 'consecutive' in the API means "
                    "consecutive among the ingested versions, not in RBI's "
                    "own publication history."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=3010",
                "expected_pages": 8,
            },
        ],
    },
    {
        "document_group": "rbi-master-circular-minority-communities-credit",
        "title": "Master Circular - Credit Facilities to Minority Communities",
        "department": "Financial Inclusion & Development Dept., Central Office",
        "document_type": "circular",
        "note": "Three consecutive annual issues: 2017, 2018, 2019.",
        "versions": [
            {
                "pdf": "06MCE03072017.pdf",
                "version_label": "2017-07-01",
                "publication_date": "2017-07-01",
                "reference_no": "RBI/2017-2018/6",
                "supersedes": None,
                "supersedes_evidence": "Chain base in this corpus.",
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=2315",
                "expected_pages": 17,
            },
            {
                "pdf": "08MC04072018.pdf",
                "version_label": "2018-07-02",
                "publication_date": "2018-07-02",
                "reference_no": "RBI/2018-19/8",
                "supersedes": "2017-07-01",
                "supersedes_evidence": (
                    "Page 1 refers to the Master Circular of July 2017 on the "
                    "same subject."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=2659",
                "expected_pages": 17,
            },
            {
                "pdf": "3MC01072019.pdf",
                "version_label": "2019-07-01",
                "publication_date": "2019-07-01",
                "reference_no": "RBI/2019-20/03",
                "supersedes": "2018-07-02",
                "supersedes_evidence": (
                    "Page 1 refers to the Master Circular of July 2018 on the "
                    "same subject."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=3007",
                "expected_pages": 17,
            },
        ],
    },
    {
        "document_group": "rbi-master-circular-lead-bank-scheme",
        "title": "Master Circular - Lead Bank Scheme",
        "department": "Financial Inclusion & Development Dept., Central Office",
        "document_type": "circular",
        "note": (
            "The long chain: 66-71 pages per issue, with substantial "
            "annexures and tables. Included deliberately -- the other two "
            "chains are short enough that alignment could look easy."
        ),
        "versions": [
            {
                "pdf": "08MCEN03072017.pdf",
                "version_label": "2017-07-03",
                "publication_date": "2017-07-03",
                "reference_no": "RBI/2017-2018/8 FIDD.CO.LBS.BC.No.1/02.01.001/2017-18",
                "supersedes": None,
                "supersedes_evidence": "Chain base in this corpus.",
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=2317",
                "expected_pages": 66,
            },
            {
                "pdf": "05MC02072018.pdf",
                "version_label": "2018-07-02",
                "publication_date": "2018-07-02",
                "reference_no": "RBI/2018-19/5 FIDD.CO.LBS.BC.No.2/02.01.001/2018-19",
                "supersedes": "2017-07-03",
                "supersedes_evidence": (
                    "Successor in the same FIDD.CO.LBS.BC series, issue "
                    "No.2/2018-19 following No.1/2017-18."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=2656",
                "expected_pages": 71,
            },
            {
                "pdf": "10MC02072019.pdf",
                "version_label": "2019-07-02",
                "publication_date": "2019-07-02",
                "reference_no": "RBI/2019-20/10 FIDD.CO.LBS.BC.No.06/02.01.001/2019-20",
                "supersedes": "2018-07-02",
                "supersedes_evidence": (
                    "Successor in the same FIDD.CO.LBS.BC series, issue "
                    "No.06/2019-20 following No.2/2018-19."
                ),
                "index_page": "https://www.rbi.org.in/commonman/english/Scripts/Notification.aspx?Id=3015",
                "expected_pages": 71,
            },
        ],
    },
]


def _flatten() -> List[Dict[str, object]]:
    """One row per document, with its chain's metadata folded in.

    ``local_filename`` is generated rather than taken from the publisher, for
    the reason ``data/README.md`` §2 gives: a name we chose is predictable and
    safe to put in a path, and the publisher's own name is recorded separately
    as ``original_filename``.
    """
    rows: List[Dict[str, object]] = []
    for chain in CHAINS:
        for index, version in enumerate(chain["versions"], start=1):
            label = str(version["version_label"])
            rows.append(
                {
                    "local_filename": (
                        f"{chain['document_group']}_{label}.pdf".replace("/", "-")
                    ),
                    "url": PDF_BASE + str(version["pdf"]),
                    "authority": "RBI",
                    "title": chain["title"],
                    "department": chain["department"],
                    "document_type": chain["document_type"],
                    "document_group": chain["document_group"],
                    "version_label": label,
                    "publication_date": version["publication_date"],
                    "reference_no": version["reference_no"],
                    "supersedes": version["supersedes"] or "",
                    "supersedes_evidence": version["supersedes_evidence"],
                    "index_page": version["index_page"],
                    "expected_pages": version["expected_pages"],
                    "chain_position": index,
                }
            )
    return rows


#: The flat download list the fetcher iterates.
SAMPLES: List[Dict[str, object]] = _flatten()
