from factorio.layout_templates import get_template, list_templates

def test_list_templates():
    templates = list_templates()
    assert "iron_smelter_4x" in templates
    assert "basic_power" in templates

def test_iron_smelter_template():
    t = get_template("iron_smelter_4x")
    assert t is not None
    furnaces = [e for e in t.entities if e["name"] == "stone-furnace"]
    assert len(furnaces) == 4

def test_template_at_offset():
    t = get_template("iron_smelter_4x")
    placed = t.at_position(10, 20)
    for e in placed:
        assert e["position"]["x"] >= 10
        assert e["position"]["y"] >= 20

def test_template_footprint():
    t = get_template("iron_smelter_4x")
    w, h = t.footprint
    assert w > 0 and h > 0
