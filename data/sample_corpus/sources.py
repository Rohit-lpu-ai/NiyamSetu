"""The Library & Search sample corpus: what to download, and from where.

**Data only.** The fetching logic is in ``fetch_sample_corpus.py``; this file
is the list, kept separate because the URLs are long and a reviewer should be
able to read the provenance without reading the downloader.

Every row was taken from the publisher's own index page on 2026-11-14 — the
page is recorded per row as ``index_page``, so a reader can re-find the
document rather than trust this file. ``title``, ``document_type``,
``document_group`` and ``version_label`` are **human-asserted** (``data/README.md``
§4); ``sha256`` is never written here, because it is computed from the bytes
that actually arrive.

Why these publishers
--------------------
``irdai.gov.in`` and ``www.nabard.org`` are the official sites of the
Insurance Regulatory and Development Authority of India and of NABARD. Both
expose direct, stable PDF links on their own index pages.

**Why not more RBI documents, when the original corpus is all RBI?** The RBI
publishes its PDFs on ``rbidocs.rbi.org.in``, and that host does not complete
a TLS handshake from this machine: TCP connects in 20 ms and the server then
never answers the ClientHello, from Python, from curl with the Windows TLS
stack, and with every TLS version forced. ``www.rbi.org.in`` (the HTML index)
is reachable, which is how these candidates were found in the first place. So
the nine RBI documents that were selected could not be downloaded here. They
are listed in ``UNREACHABLE`` below rather than quietly dropped — a corpus
whose selection was decided by a network failure should say so.

**Why not SEBI?** ``www.sebi.gov.in`` is reachable, but its circular index is
rendered client-side and exposes no PDF link; the circular pages themselves
are HTML. Guessing at attachment URLs was tried and returned 404, and
guessing further would risk putting a file of unknown provenance in the
corpus. Recorded as a limitation, not worked around.
"""

from __future__ import annotations

from typing import Dict, List

__all__ = ["SAMPLES", "UNREACHABLE", "ALLOWED_HOSTS"]

#: Only these hosts may be downloaded from. Checked before every request, so a
#: mirror or a blog cannot enter the corpus through a typo.
ALLOWED_HOSTS = frozenset({"irdai.gov.in", "www.nabard.org"})

#: Selected, verified as official, and **not** downloadable from this machine.
#: Kept so the gap in the corpus is documented and so a teammate on a
#: different network can fetch them by moving a row into SAMPLES.
UNREACHABLE: List[Dict[str, str]] = [
    {
        "title": 'Reserve Bank of India (Domestic Money Transfer) Directions, 2026',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/476MDDC5A297B05094F8096910FE97E1AD34D.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Master Directions on Cyber Resilience and Digital Payment Security Controls',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/475MDA65A2A40240B4510BB6301A33C9ECBFE.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Foreign Exchange Management (Export and Import of Goods and Services) (Second Amendment) Regulations, 2026',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/FEMA23RA03112026F5D7706996DD3493DAD9E0899C64D4793.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Implementation of Section 51A of UAPA, 1967: UNSC 1988 (2011) Taliban list update, 2026-10-30',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT30314AC275308034E98A5E239A91CEAA5A7.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Implementation of Section 51A of UAPA, 1967: UNSC 1267/1989 ISIL and Al-Qaida list update, 2026-10-27',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT302E49744F98EB44A8EACBC0B6D70E470B8.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Delegation of Compounding of certain Contraventions under FEMA, 1999 to Regional Offices',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT298AC9CBAC4415B41D5B6CE45FF58A5AB54.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Reserve Bank of India (Small Finance Banks - Miscellaneous) Amendment Directions, 2026',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT29576C2A2CDED3247308333EE2DE9383863.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Reserve Bank of India (Commercial Banks - Miscellaneous) Second Amendment Directions, 2026',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/294NT8A37BD7CAFB1463E8FCD9E5CC9F4D45B.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
    {
        "title": 'Implementation of Section 51A of UAPA, 1967: UNSC 1267/1989 ISIL and Al-Qaida list update, 2026-10-19',
        "url": 'https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NOTI2964177D6F8B5FA4B0A917BA90749E2394E.PDF',
        "authority": "RBI",
        "index_page": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "reason": "rbidocs.rbi.org.in TLS handshake times out from this machine",
    },
]

#: Everything that was actually downloaded. One dict per document; the
#: keys match the manifest columns the pipeline reads, except ``sha256``
#: and ``retrieved_at``, which the fetcher fills in from the response.
SAMPLES: List[Dict[str, str]] = [
    {
        'local_filename': 'irdai_circular_cyber-security-awareness-month_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%B0%E0%A4%BE%E0%A4%B7%E0%A5%8D%E0%A4%9F%E0%A5%8D%E0%A4%B0%E0%A5%80%E0%A4%AF+%E0%A4%B8%E0%A4%BE%E0%A4%87%E0%A4%AC%E0%A4%B0+%E0%A4%B8%E0%A5%81%E0%A4%B0%E0%A4%95%E0%A5%8D%E0%A4%B7%E0%A4%BE+%E0%A4%9C%E0%A4%BE%E0%A4%97%E0%A4%B0%E0%A5%82%E0%A4%95%E0%A4%A4%E0%A4%BE+%E0%A4%AE%E0%A4%BE%E0%A4%B9+%28%E0%A4%8F%E0%A4%A8%E0%A4%B8%E0%A5%80%E0%A4%8F%E0%A4%B8%E0%A4%8F%E0%A4%8F%E0%A4%AE%29+%E0%A4%95%E0%A4%BE+%E0%A4%85%E0%A4%A8%E0%A5%81%E0%A4%AA%E0%A4%BE%E0%A4%B2%E0%A4%A8+-+%E0%A4%85%E0%A4%95%E0%A5%8D%E0%A4%9F%E0%A5%82%E0%A4%AC%E0%A4%B0+2026+Observance+of+National+Cyber+Security+Awareness+Month+%28NCSAM%29+%E2%80%93+October+2026.pdf/f81df8af-eb6e-93b9-7607-7414e17900cc?version=1.2&t=1792046597891&download=true',
        'title': 'Observance of National Cyber Security Awareness Month (NCSAM) – October 2026.pdf',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-10-15',
        'document_group': 'irdai-national-cyber-security-awareness-month',
        'version_label': '2026',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Cyber-security circular. The IRDAI counterpart to the RBI digital'
        'payment security theme, and a near-pure-English document. Listed'
        'size on the IRDAI index: 627 KB.',
    },
    {
        'local_filename': 'irdai_master-circular_registration-capital-structure-shares_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%AC%E0%A5%80%E0%A4%AE%E0%A4%BE%E0%A4%95%E0%A4%B0%E0%A5%8D%E0%A4%A4%E0%A4%BE%E0%A4%93%E0%A4%82+%E0%A4%95%E0%A5%87+%E0%A4%AA%E0%A4%82%E0%A4%9C%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3%2C+%E0%A4%AA%E0%A5%82%E0%A4%81%E0%A4%9C%E0%A5%80+%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A5%8D%E0%A4%AF%E0%A4%BE%E0%A4%B8%2C+%E0%A4%B6%E0%A5%87%E0%A4%AF%E0%A4%B0%E0%A5%8B%E0%A4%82+%E0%A4%95%E0%A5%87+%E0%A4%85%E0%A4%82%E0%A4%A4%E0%A4%B0%E0%A4%A3+%E0%A4%94%E0%A4%B0+%E0%A4%B8%E0%A4%AE%E0%A4%BE%E0%A4%AE%E0%A5%87%E0%A4%B2%E0%A4%A8+%E0%A4%B8%E0%A4%82%E0%A4%AC%E0%A4%82%E0%A4%A7%E0%A5%80+%E0%A4%AE%E0%A4%BE%E0%A4%B8%E0%A5%8D%E0%A4%9F%E0%A4%B0+%E0%A4%AA%E0%A4%B0%E0%A4%BF%E0%A4%AA%E0%A4%A4%E0%A5%8D%E0%A4%B0%2C+2026+_+Master+Circular+on+Registration%2C+Capital+Structure%2C+Transfer+of+Shares+and+Amalgamation+of+Insurers%2C+2026.pdf/90f65b79-1afa-80cc-be02-0a5dae81913a?version=2.0&t=1794221773959&download=true',
        'title': 'Master Circular on Registration, Capital Structure, Transfer of Shares and Amalgamation of Insurers, 2026',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-11-02',
        'document_group': 'irdai-master-circular-registration-capital-structure',
        'version_label': '2026',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Master Circular: the longest IRDAI document in the sample, heavily'
        'clause-numbered, and the main test of clause-aware segmentation on'
        'a non-RBI layout. Listed size on the IRDAI index: 551 KB.',
    },
    {
        'local_filename': 'irdai_circular_formats-publishing-financial-results_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%B8%E0%A5%87%E0%A4%AC%E0%A5%80%28%E0%A4%B8%E0%A5%82%E0%A4%9A%E0%A5%80%E0%A4%AC%E0%A4%A6%E0%A5%8D%E0%A4%A7%E0%A4%A4%E0%A4%BE+%E0%A4%A6%E0%A4%BE%E0%A4%AF%E0%A4%BF%E0%A4%A4%E0%A5%8D%E0%A4%B5+%E0%A4%94%E0%A4%B0+%E0%A4%AA%E0%A5%8D%E0%A4%B0%E0%A4%95%E0%A4%9F%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3+%E0%A4%85%E0%A4%AA%E0%A5%87%E0%A4%95%E0%A5%8D%E0%A4%B7%E0%A4%BE%E0%A4%8F%E0%A4%81%29%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE%2C2015%28%E0%A4%8F%E0%A4%B2%E0%A4%93%E0%A4%A1%E0%A5%80%E0%A4%86%E0%A4%B0+%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE%2C2015%29+%E0%A4%95%E0%A5%87+%E0%A4%85%E0%A4%82%E0%A4%A4%E0%A4%B0%E0%A5%8D%E0%A4%97%E0%A4%A4+%E0%A4%85%E0%A4%AA%E0%A5%87%E0%A4%95%E0%A5%8D%E0%A4%B7%E0%A4%BF%E0%A4%A4+%E0%A4%B0%E0%A5%82%E0%A4%AA+%E0%A4%AE%E0%A5%87%E0%A4%82+%E0%A4%B5%E0%A4%BF%E0%A4%A4%E0%A5%8D%E0%A4%A4%E0%A5%80%E0%A4%AF+%E0%A4%AA%E0%A4%B0%E0%A4%BF%E0%A4%A3%E0%A4%BE%E0%A4%AE+%E0%A4%AA%E0%A5%8D%E0%A4%B0%E0%A4%95%E0%A4%BE%E0%A4%B6%E0%A4%BF%E0%A4%A4+%E0%A4%95%E0%A4%B0%E0%A4%A8%E0%A5%87+%E0%A4%B9%E0%A5%87%E0%A4%A4%E0%A5%81+%E0%A4%AB%E0%A4%BE%E0%A4%B0%E0%A5%8D%E0%A4%AE%E0%A5%87%E0%A4%9F_Formats+for+publishing+financial+results+as+reqd+under+the+SEBI%28Listing+Obligations+and+Disclosure+Re.pdf/a33b69e4-a508-9944-f517-d11eea780aed?version=2.0&t=1783688032525&download=true',
        'title': 'Formats for publishing financial results as required under the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015 (LODR Regulations, 2015)',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-07-08',
        'document_group': 'irdai-formats-publishing-financial-results-lodr',
        'version_label': '2026',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Reporting-format circular. Carries prescribed tables, so it tests'
        'the table-overlap failure mode (D-12) on a second authority.'
        'Listed size on the IRDAI index: 214 KB.',
    },
    {
        'local_filename': 'irdai_circular_surveyors-annual-fees-clarification_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%AA%E0%A4%B0%E0%A4%BF%E0%A4%AA%E0%A4%A4%E0%A5%8D%E0%A4%B0+-+%E0%A4%B8%E0%A4%B0%E0%A5%8D%E0%A4%B5%E0%A5%87%E0%A4%95%E0%A5%8D%E0%A4%B7%E0%A4%95%E0%A5%8B%E0%A4%82+%E0%A4%94%E0%A4%B0+%E0%A4%B9%E0%A4%BE%E0%A4%A8%E0%A4%BF+%E0%A4%A8%E0%A4%BF%E0%A4%B0%E0%A5%8D%E0%A4%A7%E0%A4%BE%E0%A4%B0%E0%A4%95%E0%A5%8B%E0%A4%82+%E0%A4%95%E0%A5%87+%E0%A4%B2%E0%A4%BF%E0%A4%8F+%E0%A4%B5%E0%A4%BE%E0%A4%B0%E0%A5%8D%E0%A4%B7%E0%A4%BF%E0%A4%95+%E0%A4%B6%E0%A5%81%E0%A4%B2%E0%A5%8D%E0%A4%95+%E0%A4%AD%E0%A5%81%E0%A4%97%E0%A4%A4%E0%A4%BE%E0%A4%A8+%E0%A4%B8%E0%A4%82%E0%A4%AC%E0%A4%82%E0%A4%A7%E0%A5%80+%E0%A4%B8%E0%A5%8D%E0%A4%AA%E0%A4%B7%E0%A5%8D%E0%A4%9F%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3+_+Circular+-+Surveyors+and+Loss+Assessors+clarification+regarding+payment+of+Annual+Fees.pdf/8b47078c-d4e8-9a3d-0ead-e4f0a515ea4f?version=1.0&t=1775642573794&download=true',
        'title': 'Circular - Surveyors and Loss Assessors clarification regarding payment of Annual Fees',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-04-07',
        'document_group': 'irdai-surveyors-loss-assessors-annual-fee',
        'version_label': '2026',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Clarification circular, English only. Listed size on the IRDAI'
        'index: 607 KB.',
    },
    {
        'local_filename': 'irdai_circular_transitional-arrangements-annual-fee-extension_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%B5%E0%A4%BE%E0%A4%B0%E0%A5%8D%E0%A4%B7%E0%A4%BF%E0%A4%95+%E0%A4%B6%E0%A5%81%E0%A4%B2%E0%A5%8D%E0%A4%95+%E0%A4%95%E0%A5%87+%E0%A4%AD%E0%A5%81%E0%A4%97%E0%A4%A4%E0%A4%BE%E0%A4%A8+%E0%A4%94%E0%A4%B0+%E0%A4%AA%E0%A4%82%E0%A4%9C%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3+%E0%A4%AA%E0%A5%8D%E0%A4%B0%E0%A4%AE%E0%A4%BE%E0%A4%A3%E0%A4%AA%E0%A4%A4%E0%A5%8D%E0%A4%B0+%E0%A4%95%E0%A5%87+%E0%A4%A8%E0%A4%BF%E0%A4%B0%E0%A5%8D%E0%A4%97%E0%A4%AE%E0%A4%A8+%E0%A4%95%E0%A5%87+%E0%A4%B2%E0%A4%BF%E0%A4%8F+%E0%A4%AA%E0%A4%B0%E0%A4%BF%E0%A4%B5%E0%A4%B0%E0%A5%8D%E0%A4%A4%E0%A5%80+%E0%A4%B5%E0%A5%8D%E0%A4%AF%E0%A4%B5%E0%A4%B8%E0%A5%8D%E0%A4%A5%E0%A4%BE+%E0%A4%95%E0%A4%BE+%E0%A4%B5%E0%A4%BF%E0%A4%B8%E0%A5%8D%E0%A4%A4%E0%A4%BE%E0%A4%B0+_+Extension+of+Transitional+Arrangements+for+Payment+of+Annual+Fee+and+Issuance+of+Certificate+of+Registration.pdf/dcbdf531-a720-34ef-544a-31a52d5d7a7b?version=1.0&t=1782992160826&download=true',
        'title': 'Extension of Transitional Arrangements for Payment of Annual Fee and Issuance of Certificate of Registration',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-07-02',
        'document_group': 'irdai-transitional-arrangements-annual-fee',
        'version_label': '2026-extension',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Extends an earlier arrangement. Related in subject to the'
        'surveyors circular above, which is what makes ranking between them'
        'a real test. Listed size on the IRDAI index: 176 KB.',
    },
    {
        'local_filename': 'irdai_circular_aif-investment-clarifications_2026.pdf',
        'url': 'https://irdai.gov.in/documents/37343/365525/%E0%A4%B5%E0%A5%88%E0%A4%95%E0%A4%B2%E0%A5%8D%E0%A4%AA%E0%A4%BF%E0%A4%95+%E0%A4%A8%E0%A4%BF%E0%A4%B5%E0%A5%87%E0%A4%B6+%E0%A4%A8%E0%A4%BF%E0%A4%A7%E0%A4%BF%E0%A4%AF%E0%A5%8B%E0%A4%82+%28%E0%A4%8F%E0%A4%86%E0%A4%88%E0%A4%8F%E0%A4%AB%E0%A4%8F%E0%A4%B8%29+%E0%A4%AE%E0%A5%87%E0%A4%82+%E0%A4%A8%E0%A4%BF%E0%A4%B5%E0%A5%87%E0%A4%B6+%E0%A4%95%E0%A5%87+%E0%A4%B8%E0%A4%82%E0%A4%AC%E0%A4%82%E0%A4%A7+%E0%A4%AE%E0%A5%87%E0%A4%82+%E0%A4%89%E0%A4%AA%E0%A4%AC%E0%A4%82%E0%A4%A7%E0%A5%8B%E0%A4%82+%E0%A4%B8%E0%A4%82%E0%A4%AC%E0%A4%82%E0%A4%A7%E0%A5%80+%E0%A4%B8%E0%A5%8D%E0%A4%AA%E0%A4%B7%E0%A5%8D%E0%A4%9F%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3+_+Clarifications+on+provisions+with+respect+to+investment+in+Alternative+Investment+Funds%28AIFs%29.pdf/f7d75129-66cd-99d4-2cc5-3bb4fe1f4ff4?version=2.0&t=1771505532123&download=true',
        'title': 'Clarifications on provisions with respect to investment in Alternative Investment Funds(AIFs)',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'circular',
        'reference_no': '',
        'publication_date': '2026-02-12',
        'document_group': 'irdai-alternative-investment-funds-clarifications',
        'version_label': '2026',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/circulars',
        "notes":
        'Investment-norms clarification. Listed size on the IRDAI index:'
        '393 KB.',
    },
    {
        'local_filename': 'irdai_rules_insurance-ombudsman-amendment_2023.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%AC%E0%A5%80%E0%A4%AE%E0%A4%BE+%E0%A4%B2%E0%A5%8B%E0%A4%95%E0%A4%AA%E0%A4%BE%E0%A4%B2+%28%E0%A4%B8%E0%A4%82%E0%A4%B6%E0%A5%8B%E0%A4%A7%E0%A4%A8%29+%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE%2C+2023+_+Insurance+Ombudsman+%28Amendment%29Rules%2C+2023.pdf/6a8d58e3-da6e-a45f-1865-fb6fdb20736b?version=1.1&t=1700733169946&download=true',
        'title': 'Insurance Ombudsman (Amendment)Rules, 2023',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'amendment',
        'reference_no': '',
        'publication_date': '2023-11-09',
        'document_group': 'insurance-ombudsman-rules',
        'version_label': '2023-amendment',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        'Statutory rules, 2023: widens the corpus date range by three'
        'years. Listed size on the IRDAI index: 1.1 MB.',
    },
    {
        'local_filename': 'irdai_regulations_insurance-intermediaries-amendment_2022.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%86%E0%A4%88%E0%A4%86%E0%A4%B0%E0%A4%A1%E0%A5%80%E0%A4%8F%E0%A4%86%E0%A4%88+%E0%A4%AC%E0%A5%80%E0%A4%AE%E0%A4%BE+%E0%A4%AE%E0%A4%A7%E0%A5%8D%E0%A4%AF%E0%A4%B8%E0%A5%8D%E0%A4%A5+%28%E0%A4%B8%E0%A4%82%E0%A4%B6%E0%A5%8B%E0%A4%A7%E0%A4%A8%29+%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE+2022+_++IRDAI+Insurance+Intermediaries+%28Amendment%29+Regulations+2022.pdf/72e2da55-495f-2343-51a5-538b401d5168?version=1.1&t=1688449954298&download=true',
        'title': 'IRDAI Insurance Intermediaries (Amendment) Regulations 2022',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'amendment',
        'reference_no': '',
        'publication_date': '2022-12-08',
        'document_group': 'irdai-insurance-intermediaries-regulations',
        'version_label': '2022-amendment',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        'Amendment regulations, 2022. Listed size on the IRDAI index: 1.1'
        'MB.',
    },
    {
        'local_filename': 'irdai_regulations_other-forms-of-capital_2022.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%86%E0%A4%88%E0%A4%86%E0%A4%B0%E0%A4%A1%E0%A5%80%E0%A4%8F%E0%A4%86%E0%A4%88+%28%E0%A4%85%E0%A4%A8%E0%A5%8D%E0%A4%AF+%E0%A4%AA%E0%A5%8D%E0%A4%B0%E0%A4%95%E0%A4%BE%E0%A4%B0+%E0%A4%95%E0%A5%80+%E0%A4%AA%E0%A5%82%E0%A4%81%E0%A4%9C%E0%A5%80%29+%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE%2C+2022+_+IRDAI+%28Other+Forms+of+Capital%29+Regulations%2C+2022.pdf/46ca7deb-2573-8020-bad6-5655c1707a3f?version=1.1&t=1688450321874&download=true',
        'title': 'IRDAI (Other Forms of Capital) Regulations, 2022',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'guideline',
        'reference_no': '',
        'publication_date': '2022-12-08',
        'document_group': 'irdai-other-forms-of-capital-regulations',
        'version_label': '2022',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        "Capital regulations: typed 'guideline' because it is a standalone"
        "instrument, not an amendment. Relevant to the 'capital"
        "requirements' query theme from a different regulator. Listed size"
        'on the IRDAI index: 1.6 MB.',
    },
    {
        'local_filename': 'irdai_notification_obligatory-cession-fy2025-26.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%B5%E0%A4%BF%E0%A4%A4%E0%A5%8D%E0%A4%A4%E0%A5%80%E0%A4%AF+%E0%A4%B5%E0%A4%B0%E0%A5%8D%E0%A4%B7+2025-26+%E0%A4%95%E0%A5%87+%E0%A4%B2%E0%A4%BF%E0%A4%8F+%E0%A4%85%E0%A4%A8%E0%A4%BF%E0%A4%B5%E0%A4%BE%E0%A4%B0%E0%A5%8D%E0%A4%AF+%E0%A4%85%E0%A4%A7%E0%A5%8D%E0%A4%AF%E0%A4%B0%E0%A5%8D%E0%A4%AA%E0%A4%A3+_+Obligatory+Cession+for+the+financial+year+2025-26.pdf/38f7aeca-f5b2-ef2c-0a0e-94323a19aa13?version=1.0&t=1740733994485&download=true',
        'title': 'Obligatory Cession for the financial year 2025-26',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'notification',
        'reference_no': '',
        'publication_date': '2025-02-28',
        'document_group': 'irdai-obligatory-cession',
        'version_label': 'fy2025-26',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        'Gazette-style notification. Larger file for its page count, which'
        'usually means scanned pages - kept deliberately to exercise the'
        'scanned-document path (D-6) on real data. Listed size on the IRDAI'
        'index: 1.8 MB.',
    },
    {
        'local_filename': 'irdai_notification_de-notification-of-all-tariffs_2024.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%B8%E0%A4%AD%E0%A5%80+%E0%A4%AA%E0%A5%8D%E0%A4%B0%E0%A4%B6%E0%A5%81%E0%A4%B2%E0%A5%8D%E0%A4%95%E0%A5%8B%E0%A4%82+%E0%A4%95%E0%A5%80+%E0%A4%85%E0%A4%A7%E0%A4%BF%E0%A4%B8%E0%A5%82%E0%A4%9A%E0%A4%A8%E0%A4%BE+%E0%A4%A8%E0%A4%BF%E0%A4%B0%E0%A4%B8%E0%A5%8D%E0%A4%A4%E0%A5%80%E0%A4%95%E0%A4%B0%E0%A4%A3+_+De-Notification+of+All+Tariffs.pdf/b0d027f1-ce91-816f-8e67-633275b0d862?version=1.1&t=1712311034900&download=true',
        'title': 'De-Notification of All Tariffs',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'notification',
        'reference_no': '',
        'publication_date': '2024-04-01',
        'document_group': 'irdai-de-notification-of-tariffs',
        'version_label': '2024',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        'Short notification, 2024. Listed size on the IRDAI index: 748 KB.',
    },
    {
        'local_filename': 'irdai_rules_indian-insurance-companies-foreign-investment-amendment_2021.pdf',
        'url': 'https://irdai.gov.in/documents/37343/370706/%E0%A4%AD%E0%A4%BE%E0%A4%B0%E0%A4%A4%E0%A5%80%E0%A4%AF+%E0%A4%AC%E0%A5%80%E0%A4%AE%E0%A4%BE+%E0%A4%95%E0%A4%82%E0%A4%AA%E0%A4%A8%E0%A5%80+%28%E0%A4%B5%E0%A4%BF%E0%A4%A6%E0%A5%87%E0%A4%B6%E0%A5%80+%E0%A4%B5%E0%A4%BF%E0%A4%A8%E0%A4%BF%E0%A4%A7%E0%A4%BE%E0%A4%A8%29+%E0%A4%B8%E0%A4%82%E0%A4%B6%E0%A5%8B%E0%A4%A7%E0%A4%A8+%E0%A4%A8%E0%A4%BF%E0%A4%AF%E0%A4%AE%2C+2021+_+Indian+Insurance+Companies+%28Foreign+Investment%29+Amendment+Rules%2C+2021.pdf/15a8001b-713a-79f5-8728-e9e4fc3a7183?version=1.2&t=1688452709085&download=true',
        'title': 'Indian Insurance Companies (Foreign Investment) Amendment Rules, 2021',
        'department': 'Insurance Regulatory and Development Authority of India',
        'document_type': 'amendment',
        'reference_no': '',
        'publication_date': '2021-05-20',
        'document_group': 'indian-insurance-companies-foreign-investment-rules',
        'version_label': '2021-amendment',
        'authority': 'IRDAI',
        'index_page': 'https://irdai.gov.in/notifications',
        "notes":
        'Oldest document in the sample (2021), so date filtering and date'
        'sorting have a real spread to work with. Listed size on the IRDAI'
        'index: 1.2 MB.',
    },
    {
        'local_filename': 'nabard_policy_gender-policy_nabard.pdf',
        'url': 'https://www.nabard.org/auth/writereaddata/File/gender-policy-of-nabard.pdf',
        'title': 'Gender Policy of NABARD',
        'department': 'National Bank for Agriculture and Rural Development',
        'document_type': 'guideline',
        'reference_no': '',
        'publication_date': '',
        'document_group': 'nabard-gender-policy',
        'version_label': 'v1',
        'authority': 'NABARD',
        'index_page': 'https://www.nabard.org/circulars.aspx',
        "notes":
        'A policy document rather than a circular, so the corpus is not all'
        'one genre. Publication date left blank: not printed on the'
        'document in a verifiable form.',
    },
    {
        'local_filename': 'nabard_policy_gender-policy-glossary_nabard.pdf',
        # EXCLUDED from the search corpus, deliberately and with evidence.
        # It is a 372-page English-Hindi banking dictionary whose Hindi is set
        # in a legacy non-Unicode font: extraction returns 780k characters of
        # which 44.8% are ASCII letters and ZERO are Devanagari codepoints --
        # i.e. the Hindi came back as mojibake ("ie e'eceerCe efJekeAeme"),
        # exactly the failure Experiment A found no extractor fixes. Three
        # pages extract nothing at all, so the version is 'partial'.
        #
        # Indexing it would put 372 pages of corrupt text into a 'english'
        # tsvector and let it dominate every segment count in the corpus. It
        # is also a dictionary, not a policy document. Kept in this list, and
        # downloaded, so the decision is auditable rather than invisible.
        'excluded_reason':
            'extraction corrupt (legacy non-Unicode Hindi font, 0 Devanagari '
            'codepoints recovered from 780k characters); 372-page bilingual '
            'dictionary, not a policy document; extraction_status partial',
        'url': 'https://www.nabard.org/auth/writereaddata/File/glossary.pdf',
        'title': 'Glossary of Terms (NABARD)',
        'department': 'National Bank for Agriculture and Rural Development',
        'document_type': 'other',
        'reference_no': '',
        'publication_date': '',
        'document_group': 'nabard-glossary-of-terms',
        'version_label': 'v1',
        'authority': 'NABARD',
        'index_page': 'https://www.nabard.org/circulars.aspx',
        "notes":
        "A definitions document: typed 'other'. Included because a glossary"
        'is almost entirely short definition entries, which is a different'
        'segmentation shape from a circular.',
    },
]
