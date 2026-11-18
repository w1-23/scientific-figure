"""P2 bounded local retrieval, honest coverage and fixed construction contracts."""
from copy import deepcopy
from importlib.resources import files
import json
import shutil

import pytest

from scientific_figure import recipes, registry


def candidate(result, identity):
    return next(item for item in result["candidates"] if item["id"] == identity)


@pytest.fixture
def local_catalog(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(files("scientific_figure").joinpath("data", "registry"), root)
    return root


@pytest.mark.parametrize("payload", [
    None, [], "cell", {"query": ""}, {"query": "  "}, {"query": 1},
    {"query": "cell", "limit": True}, {"query": "cell", "limit": 1.0},
    {"query": "cell", "limit": 0}, {"query": "cell", "limit": 11},
    {"query": "cell", "required_parts": "membrane"},
    {"query": "cell", "required_parts": ("membrane",)},
    {"query": "cell", "required_parts": [1]},
    {"query": "cell", "required_parts": [""]},
    {"query": "cell", "style": None}, {"query": "cell", "style": {}},
    {"query": "cell", "style": " "},
    {"query": "cell", "scene_version": "0.4"},
    {"query": "cell", "scene_version": 0.3},
    {"query": "cell", "scene_version": []},
    {"query": "cell", "module": "os.system"},
    {"query": "cell", "registry": "/tmp/catalog.json"},
    {"query": "cell", "license": "MIT"},
    {"query": "cell", "schema_version": "0.1"},
    {"query": "cell", "unknown": float("nan")},
    {"query": "\ud800"},
])
def test_request_is_strict_json_and_rejected_before_catalog(payload, monkeypatch):
    def forbidden(*args):
        pytest.fail("invalid request reached the catalog")
    monkeypatch.setattr(registry, "_catalog", forbidden)
    with pytest.raises(ValueError):
        registry.search(payload)


@pytest.mark.parametrize("payload", [
    {"query": "x" * 513},
    {"query": " ".join(f"term{i}" for i in range(65))},
    {"query": "cell", "required_parts": ["a"] * 33},
    {"query": "cell", "required_parts": ["a" * 101]},
    {"query": "cell", "style": "a" * 129},
    {"query": "cell", "required_parts": ["部" * 100] * 32},
])
def test_work_budgets(payload):
    with pytest.raises(ValueError):
        registry.search(payload)


def test_budget_boundaries_accept_json_requests():
    assert registry.search({"query": "x" * 512})["candidates"] == []
    result = registry.search({"query": " ".join(f"x{i}" for i in range(64)),
                              "required_parts": ["p" * 100] * 32,
                              "style": "s" * 128, "scene_version": "0.3", "limit": 10})
    assert result["candidates"] == []


def test_limitation_word_hit_is_not_reported_as_supported_structure():
    item = candidate(registry.search({"query": "organelles", "limit": 10}), "cell_symbol")
    assert {hit["field"] for hit in item["match_basis"]} == {"limitations"}
    assert item["fit"] == "needs_review"


@pytest.mark.parametrize("query,identity", [
    ("cell", "cell_symbol"), ("细胞", "cell_symbol"),
    ("绘制一个细胞", "cell_symbol"), ("nanoparticle", "particle"),
    ("纳米颗粒", "particle"), ("lemon", "twemoji.lemon"),
    ("柠檬", "twemoji.lemon"), ("箭头", "arrow"),
])
def test_english_and_chinese_queries_only_return_real_pins(query, identity):
    result = registry.search({"query": query, "limit": 10})
    assert candidate(result, identity)["matched_terms"]
    known = {(entry["id"], entry["version"], entry["kind"]) for entry in registry.list_entries()}
    for item in result["candidates"]:
        assert (item["id"], item["version"], item["kind"]) in known
        assert item["match_basis"] and item["retrieval_score"] > 0
    assert result["selection"] == "host_required"
    assert "not semantic confidence" in result["score_scope"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("query", ["mouse", "flask", "avatar", "不存在的品牌标志", "qzunknown", "..."])
def test_unmatched_query_never_uses_generic_required_parts_as_a_hit(query):
    result = registry.search({"query": query, "required_parts": ["body", "root", "label"],
                              "style": "cell", "limit": 10})
    assert result["candidates"] == []
    assert result["fallback"]["kind"] == "program"
    assert result["fallback"]["automatic"] is False


def test_complex_cell_exposes_missing_parts_and_requires_program_fallback():
    request = {"query": "细胞", "required_parts": ["细胞膜", "细胞核", "mitochondria", "golgi"],
               "scene_version": "0.3"}
    before = deepcopy(request)
    result = registry.search(request)
    cell = candidate(result, "cell_symbol")
    assert cell["missing_parts"] == ["mitochondria", "golgi"]
    assert cell["fit"] == "partial"
    assert cell["part_coverage"][1]["part_key"] == "nucleus (optional)"
    assert "program" in " ".join(cell["fit_reasons"])
    assert result["fallback"]["kind"] == "program"
    assert result["selection"] == "host_required"
    assert request == before


def test_parts_are_exact_declared_keys_not_words_from_descriptions_or_ports():
    result = registry.search({"query": "particle", "required_parts": ["shell", "payload.2", "left", "radius", "payload.2.fake"]})
    item = candidate(result, "particle")
    assert item["missing_parts"] == ["left", "radius", "payload.2.fake"]
    assert item["part_coverage"][1]["part_key"] == "payload.N"
    assert item["part_coverage"][1]["status"] == "declared_requires_params"


def test_contract_reuses_describe_and_keeps_unset_license_visible():
    result = registry.search({"query": "particle", "scene_version": "0.3"})
    item = candidate(result, "particle")
    assert item["recipe_spec"] == recipes.describe("particle", version=item["version"])
    assert item["recipe_spec"]["params_schema"]["properties"]["radius"]["maximum"] == 500
    assert item["license"]["identifier"] == item["distribution"]["scope"] == "project_license_unset"
    assert item["license"]["notice"] and item["source"]["author"]
    assert "not public distribution permission" in result["license_scope"]
    item["recipe_spec"]["part_keys"].clear()
    assert candidate(registry.search({"query": "particle"}), "particle")["recipe_spec"]["part_keys"]


@pytest.mark.parametrize("query,field", [("payload_count", "recipe_spec.params_schema"),
                                          ("cytoplasm", "recipe_spec.part_keys"),
                                          ("spine", "recipe_spec.ports")])
def test_query_indexes_trusted_params_parts_and_ports(query, field):
    result = registry.search({"query": query, "limit": 10})
    assert any(hit["field"] == field for item in result["candidates"] for hit in item["match_basis"])


def test_family_is_metadata_only_even_when_related_recipe_declares_parts():
    item = candidate(registry.search({"query": "family.cell_symbol", "required_parts": ["membrane"], "limit": 10}),
                     "family.cell_symbol")
    assert item["fit"] == "metadata_only"
    assert item["constructible"] is False
    assert item["missing_parts"] == ["membrane"]
    assert "recipe_spec" not in item and "asset" not in item
    assert item["related_recipes"] == ["cell_symbol", "simple_microbe"]


def test_asset_keeps_license_import_limits_and_unknown_semantic_parts():
    item = candidate(registry.search({"query": "柠檬", "required_parts": ["seeds"]}), "twemoji.lemon")
    actual = registry.resolve("twemoji.lemon", version="14.0.2")
    for key in ("license", "source", "distribution", "asset", "limitations", "protected", "modifications"):
        assert item[key] == actual[key]
    assert item["fit"] == "partial" and item["missing_parts"] == ["seeds"]
    assert item["import_status"] == "not_run"
    assert item["capabilities"]["importer"] == "strict_svg"
    assert item["license"]["identifier"] == "CC-BY-4.0"


def test_scene_minimum_and_unknown_style_are_not_assumed_supported():
    old = registry.search({"query": "interwoven_bands", "scene_version": "0.1", "limit": 1})
    assert old["candidates"][0]["scene_fit"] == "unsupported"
    assert old["candidates"][0]["fit"] == "unsupported"
    assert old["fallback"]["kind"] == "program"
    current = registry.search({"query": "interwoven_bands", "scene_version": "0.2", "limit": 1})
    assert current["candidates"][0]["fit"] == "compatible"
    styled = registry.search({"query": "particle", "style": "photorealistic", "limit": 1})
    assert styled["candidates"][0]["style_fit"] == "unknown"
    assert styled["candidates"][0]["fit"] == "needs_review"


def test_sort_is_stable_under_catalog_reorder_and_ties(local_catalog):
    manifest = local_catalog / "catalog.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    lemon = deepcopy(data["entries"][0])
    lemon["id"] = "other.lemon"
    data["entries"].append(lemon)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    request = {"query": "lemon", "limit": 10}
    first = registry.search(request, registry=local_catalog)
    data["entries"].reverse()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert registry.search(request, registry=manifest) == first
    ordering = [(-item["retrieval_score"], item["id"], item["version"], item["kind"]) for item in first["candidates"]]
    assert ordering == sorted(ordering)
    assert [item["id"] for item in first["candidates"]][:2] == ["other.lemon", "twemoji.lemon"]
    assert registry.search({**request, "limit": 1}, registry=local_catalog)["candidates"] == first["candidates"][:1]


@pytest.mark.parametrize("which", ["asset", "license", "missing_asset"])
def test_matched_files_are_verified_without_importing_or_building(local_catalog, monkeypatch, which):
    entry = registry.resolve("twemoji.lemon", registry=local_catalog)
    path = local_catalog / (entry["license"]["text_path"] if which == "license" else entry["asset"]["path"])
    if which == "missing_asset":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    monkeypatch.setattr(registry, "import_svg", lambda *a, **k: pytest.fail("search imported SVG"))
    monkeypatch.setattr(recipes, "build", lambda *a, **k: pytest.fail("search built geometry"))
    with pytest.raises(ValueError):
        registry.search({"query": "lemon"}, registry=local_catalog)


def test_valid_search_does_not_import_assets_or_execute_source(monkeypatch):
    monkeypatch.setattr(registry, "import_svg", lambda *a, **k: pytest.fail("search imported SVG"))
    monkeypatch.setattr(recipes, "build", lambda *a, **k: pytest.fail("search built geometry"))
    assert candidate(registry.search({"query": "lemon"}), "twemoji.lemon")
    assert candidate(registry.search({"query": "cell"}), "cell_symbol")
