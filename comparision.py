import io
import re
from dataclasses import dataclass
from typing import List, Dict, Tuple

import pandas as pd
import streamlit as st
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

st.set_page_config(page_title="NiyamSetu | Policy Comparator", page_icon="⚖️", layout="wide")

@dataclass
class Clause:
    doc_name: str
    page: int
    clause_id: str
    text: str


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(uploaded_file) -> List[Tuple[int, str]]:
    reader = PdfReader(io.BytesIO(uploaded_file.getvalue()))
    pages = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except TypeError:
            text = page.extract_text() or ""
        pages.append((page_no, clean_text(text)))
    return pages


def split_clauses(doc_name: str, pages: List[Tuple[int, str]]) -> List[Clause]:
    clauses = []
    counter = 0
    # Preserve page provenance. Split on numbered legal clauses first, then paragraphs.
    heading = re.compile(r"(?m)(?=^\s*(?:\d+(?:\.\d+){0,4}[.)]?|[A-Z]\.[0-9]+|SECTION\s+\d+|Article\s+\d+)\s+)", re.I)
    for page_no, text in pages:
        if not text:
            continue
        pieces = [p.strip() for p in heading.split(text) if p.strip()]
        if len(pieces) <= 1:
            pieces = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for piece in pieces:
            piece = re.sub(r"\s+", " ", piece).strip()
            if len(piece) < 35:
                continue
            counter += 1
            # Keep explicit numbering where available; otherwise use a stable generated ID.
            m = re.match(r"((?:\d+(?:\.\d+){0,4}[.)]?|[A-Z]\.\d+|SECTION\s+\d+|Article\s+\d+))", piece, re.I)
            clause_id = m.group(1) if m else f"P{page_no}-C{counter}"
            clauses.append(Clause(doc_name, page_no, clause_id, piece))
    return clauses


def similarity_matrix(old: List[Clause], new: List[Clause]):
    texts = [c.text for c in old] + [c.text for c in new]
    if not texts:
        return None
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", max_features=30000)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return None
    return cosine_similarity(matrix[:len(old)], matrix[len(old):])


def align_clauses(old: List[Clause], new: List[Clause], threshold: float = 0.25):
    sim = similarity_matrix(old, new)
    if sim is None:
        return [], list(old), list(new)
    candidates = []
    for i in range(len(old)):
        for j in range(len(new)):
            if sim[i, j] >= threshold:
                candidates.append((float(sim[i, j]), i, j))
    candidates.sort(reverse=True)
    used_old, used_new, matches = set(), set(), []
    for score, i, j in candidates:
        if i not in used_old and j not in used_new:
            used_old.add(i); used_new.add(j)
            matches.append((old[i], new[j], score))
    removed = [c for i, c in enumerate(old) if i not in used_old]
    added = [c for j, c in enumerate(new) if j not in used_new]
    return matches, removed, added


def extract_facts(text: str) -> Dict[str, List[str]]:
    patterns = {
        "Dates / deadlines": r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b",
        "Time periods": r"\b\d+\s+(?:days?|weeks?|months?|years?)\b",
        "Amounts / percentages": r"(?:₹\s?[\d,]+(?:\.\d+)?|\b\d+(?:\.\d+)?\s?%)",
        "Obligation language": r"\b(?:shall|must|required to|is required to|will have to|may not|should)\b",
    }
    facts = {}
    for label, pattern in patterns.items():
        found = list(dict.fromkeys(re.findall(pattern, text, flags=re.I)))
        if found:
            facts[label] = found[:8]
    return facts


def explain_change(old: Clause, new: Clause, score: float) -> str:
    old_l, new_l = old.text.lower(), new.text.lower()
    old_dates, new_dates = extract_facts(old.text), extract_facts(new.text)
    if old_l == new_l:
        return "The wording is unchanged in the extracted text."
    details = []
    for label in ("Dates / deadlines", "Time periods", "Amounts / percentages"):
        a, b = old_dates.get(label, []), new_dates.get(label, [])
        if a != b and (a or b):
            details.append(f"{label.lower()} changed from {', '.join(a) if a else 'not stated'} to {', '.join(b) if b else 'not stated'}")
    old_ob = bool(old_dates.get("Obligation language")); new_ob = bool(new_dates.get("Obligation language"))
    if old_ob != new_ob:
        details.append("obligation-related wording has changed")
    if details:
        return "The clauses cover similar material, but " + "; ".join(details) + ". Review the source wording to confirm the practical and legal effect."
    if score >= 0.75:
        return "The clause is mostly similar, but some wording has changed. Review the side-by-side text for the exact change and its effect."
    return "This clause appears to have been substantially rewritten while remaining related to the earlier clause. The precise legal effect needs human review."


def pct(x):
    return f"{x * 100:.1f}%"

st.title("⚖️ NiyamSetu")
st.subheader("Government Policy Comparison Engine")
st.write("Upload 2–4 policy PDFs to identify similar, modified, added and removed clauses, with page-level evidence.")

with st.sidebar:
    st.header("How it works")
    st.caption("PDF text extraction → clause segmentation → TF-IDF similarity → clause alignment → plain-language explanation")
    st.info("This prototype compares extracted text. Scanned/image-only PDFs need OCR and are not reliably supported in this version.")

uploads = st.file_uploader("Upload government policy PDFs", type=["pdf"], accept_multiple_files=True, help="Upload between 2 and 4 PDFs. Use clear filenames such as Policy_2024.pdf and Amendment_2026.pdf.")
if uploads:
    if len(uploads) < 2 or len(uploads) > 4:
        st.warning("Please upload between 2 and 4 PDF documents.")
    else:
        docs = {}
        with st.spinner("Reading PDFs and extracting clauses..."):
            for f in uploads:
                pages = extract_pdf(f)
                clauses = split_clauses(f.name, pages)
                docs[f.name] = {"pages": pages, "clauses": clauses}
        overview = pd.DataFrame([{"Document": name, "Pages": len(data["pages"]), "Extracted clauses": len(data["clauses"]), "Text extracted": sum(bool(t) for _, t in data["pages"])} for name, data in docs.items()])
        st.markdown("### Uploaded documents")
        st.dataframe(overview, use_container_width=True, hide_index=True)
        names = list(docs.keys())
        c1, c2 = st.columns(2)
        with c1:
            old_name = st.selectbox("Older / reference policy", names, index=0)
        with c2:
            new_options = [n for n in names if n != old_name]
            new_name = st.selectbox("Newer / policy to compare", new_options, index=0)
        old_clauses, new_clauses = docs[old_name]["clauses"], docs[new_name]["clauses"]
        if not old_clauses or not new_clauses:
            st.error("Could not extract enough selectable text from one or both PDFs. They may be scanned; OCR support is needed.")
        else:
            threshold = st.slider("Minimum clause similarity for matching", 0.10, 0.80, 0.25, 0.05, help="Lower values match more loosely related clauses. Higher values may classify rewritten clauses as removed + added.")
            matches, removed, added = align_clauses(old_clauses, new_clauses, threshold)
            scores = [score for _, _, score in matches]
            avg_similarity = sum(scores) / len(scores) if scores else 0.0
            total = len(matches) + len(removed) + len(added)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Matched clauses", len(matches))
            m2.metric("Modified / similar", sum(1 for a,b,s in matches if a.text != b.text))
            m3.metric("Added", len(added))
            m4.metric("Removed", len(removed))
            st.metric("Average similarity of matched clauses", pct(avg_similarity) if scores else "N/A")
            st.caption("This is the average TF-IDF similarity of matched clause pairs, not a legal-equivalence score or an overall policy similarity score.")

            st.markdown("### Policy differences")
            filter_choice = st.radio("Show", ["All changes", "Modified", "Added", "Removed", "Similar / unchanged"], horizontal=True)
            items = []
            for a, b, score in matches:
                status = "Similar / unchanged" if a.text == b.text else "Modified"
                items.append({"status": status, "old": a, "new": b, "score": score})
            for c in added: items.append({"status": "Added", "old": None, "new": c, "score": None})
            for c in removed: items.append({"status": "Removed", "old": c, "new": None, "score": None})
            shown = [x for x in items if filter_choice == "All changes" or x["status"] == filter_choice]
            if not shown:
                st.info("No items in this category for the selected documents.")
            for idx, item in enumerate(shown, start=1):
                status = item["status"]
                with st.expander(f"{idx}. {status} — " + (f"similarity {pct(item['score'])}" if item["score"] is not None else "no matching clause"), expanded=(idx == 1)):
                    left, right = st.columns(2)
                    with left:
                        st.markdown(f"**OLD — {old_name}**")
                        if item["old"]:
                            st.caption(f"Page {item['old'].page} · Clause {item['old'].clause_id}")
                            st.write(item["old"].text)
                        else: st.info("No matching clause found in the older policy.")
                    with right:
                        st.markdown(f"**NEW — {new_name}**")
                        if item["new"]:
                            st.caption(f"Page {item['new'].page} · Clause {item['new'].clause_id}")
                            st.write(item["new"].text)
                        else: st.info("No matching clause found in the new policy.")
                    if status == "Modified":
                        st.markdown("**Plain-language explanation**")
                        st.write(explain_change(item["old"], item["new"], item["score"]))
                    elif status == "Added":
                        st.markdown("**Plain-language explanation**")
                        st.write("This requirement or clause appears in the new document, and no sufficiently similar clause was found in the older document. Check the cited page to understand who it applies to and what it requires.")
                    elif status == "Removed":
                        st.markdown("**Plain-language explanation**")
                        st.write("This clause appears in the older document, but no sufficiently similar clause was found in the new document. This may indicate removal, renumbering or substantial rewriting; verify against the complete notification.")
                    else:
                        st.write("The extracted wording matches exactly.")
                    source_rows = []
                    if item["old"]: source_rows.append({"Version": "Old", "Document": old_name, "Page": item["old"].page, "Clause": item["old"].clause_id})
                    if item["new"]: source_rows.append({"Version": "New", "Document": new_name, "Page": item["new"].page, "Clause": item["new"].clause_id})
                    st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)

            st.markdown("### Extracted key information")
            for label, clause_list in (("Older policy", old_clauses), ("Newer policy", new_clauses)):
                with st.expander(label + " — dates, deadlines and obligation indicators"):
                    rows = []
                    for clause in clause_list:
                        facts = extract_facts(clause.text)
                        if facts:
                            rows.append({"Page": clause.page, "Clause": clause.clause_id, **{k: ", ".join(v) for k,v in facts.items()}})
                    if rows: st.dataframe(pd.DataFrame(rows).fillna(""), use_container_width=True, hide_index=True)
                    else: st.caption("No dates, periods, amounts or obligation keywords were detected by the current rules.")

            report_rows = []
            for item in items:
                report_rows.append({
                    "Change type": item["status"],
                    "Similarity": round(item["score"], 4) if item["score"] is not None else "",
                    "Old document": old_name,
                    "Old page": item["old"].page if item["old"] else "",
                    "Old clause": item["old"].clause_id if item["old"] else "",
                    "Old text": item["old"].text if item["old"] else "",
                    "New document": new_name,
                    "New page": item["new"].page if item["new"] else "",
                    "New clause": item["new"].clause_id if item["new"] else "",
                    "New text": item["new"].text if item["new"] else "",
                })
            csv = pd.DataFrame(report_rows).to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Download comparison report (CSV)", csv, file_name="niyamsetu_policy_comparison.csv", mime="text/csv")
            st.caption("Important: clause matching is an NLP-assisted first pass. Similarity thresholds, clause numbering changes, tables, scanned PDFs and legal context can affect results. A human should verify every policy change before acting on it.")
