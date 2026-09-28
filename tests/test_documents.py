"""Document collections: complete, consistent with the marts, the same built in parallel or serially,
and the store's upsert semantics."""

from __future__ import annotations

from datetime import date

from pophealth.documents.build import build_documents, collect, load_collections
from pophealth.documents.docstore import DocumentStore
from pophealth.warehouse import Warehouse


def test_one_document_per_patient_clinic_and_care_manager(scenario):
    store = DocumentStore(scenario.settings.docstore, "restricted")
    assert store.count("patients") == scenario.scalar("select count(*) from published.patients")
    assert store.count("clinics") == 12
    managers = scenario.scalar("select count(distinct care_manager_name) from marts.care_gaps")
    assert store.count("worklists") == managers
    clinics = {c["_id"] for c in store.find("clinics")}
    assert all(p["site"]["id"] in clinics for p in store.find("patients"))


def test_worklists_carry_every_open_gap(scenario):
    store = DocumentStore(scenario.settings.docstore, "restricted")
    in_docs = sum(w["openGaps"] for w in store.find("worklists"))
    assert in_docs == scenario.scalar("select count(*) from marts.care_gaps")


def test_a_worklist_can_span_clinics_and_a_partial_build_never_splits_it(scenario):
    store = DocumentStore(scenario.settings.docstore, "restricted")
    worklists = store.find("worklists")
    assert any(len(w["sites"]) > 1 for w in worklists)  # patients who moved kept their care manager
    report = build_documents(scenario.settings, date(2026, 3, 3), sites=["S03"], dry_run=True)
    patients_at_s03 = scenario.scalar("select count(*) from published.patients where site_id = 'S03'")
    assert report["patients"]["built"] == patients_at_s03  # partitioned: only the clinic asked for
    assert report["worklists"]["built"] == len(worklists)  # not partitioned: always whole
    assert report["clinics"]["built"] == store.count("clinics")


def test_parallel_build_equals_serial_build(scenario):
    spec = load_collections(scenario.settings)["patients"]
    with Warehouse(scenario.settings.warehouse_path) as wh:
        sites = [r["site_id"] for r in wh.rows("select site_id from ref.sites order by 1")]
        serial = collect(wh, spec, sites, date(2026, 3, 3), workers=1)
        parallel = collect(wh, spec, sites, date(2026, 3, 3), workers=4)
    key = lambda d: d["_id"]  # noqa: E731
    assert sorted(serial, key=key) == sorted(parallel, key=key)


def test_docstore_upsert_semantics(tmp_path):
    store = DocumentStore(tmp_path, "db")
    assert store.sync("c", [{"_id": "a", "v": 1}, {"_id": "b", "v": 1}]) == {
        "inserted": 2,
        "updated": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    assert store.sync("c", [{"_id": "a", "v": 2}, {"_id": "c", "v": 1}], partial=True) == {
        "inserted": 1,
        "updated": 1,
        "unchanged": 0,
        "deleted": 0,
    }
    assert store.sync("c", [{"_id": "a", "v": 2}]) == {"inserted": 0, "updated": 0, "unchanged": 1, "deleted": 2}
    assert store.find("c") == [{"_id": "a", "v": 2}]
    first = (tmp_path / "db" / "c.jsonl").read_bytes()
    store.sync("c", [{"v": 2, "_id": "a"}])
    assert (tmp_path / "db" / "c.jsonl").read_bytes() == first
