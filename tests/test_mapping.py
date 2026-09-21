from fccsv.domain import mapping as m


def test_normalize_name():
    assert m.normalize_name("Plant Data") == "plantdata"
    assert m.normalize_name("plant-data") == "plantdata"
    assert m.normalize_name(" Plant_Data ") == "plantdata"


def test_infer_key_column_prefers_id():
    assert m.infer_key_column(["ShopId", "Name"]) == "ShopId"
    assert m.infer_key_column(["Name", "Id"]) == "Id"
    assert m.infer_key_column(["Name"]) == "Name"


def test_infer_key_column_composite_when_single_repeats():
    headers = ["ShopId", "SlotIndex", "Price"]
    rows = [
        {"ShopId": "1", "SlotIndex": "0", "Price": "10"},
        {"ShopId": "1", "SlotIndex": "1", "Price": "20"},
        {"ShopId": "2", "SlotIndex": "0", "Price": "10"},
    ]
    assert m.infer_key_column(headers, rows) == "ShopId,SlotIndex"


def test_infer_key_column_prefers_sequence_partner():
    headers = ["ItemId", "SpriteKey", "SortOrder"]
    rows = [
        {"ItemId": "1", "SpriteKey": "a", "SortOrder": "0"},
        {"ItemId": "1", "SpriteKey": "b", "SortOrder": "1"},
    ]
    assert m.infer_key_column(headers, rows) == "ItemId,SortOrder"


def test_infer_key_column_positional_when_nothing_unique():
    headers = ["RowKind", "Key"]
    rows = [
        {"RowKind": "Mission", "Key": "a"},
        {"RowKind": "Mission", "Key": "a"},
    ]
    assert m.infer_key_column(headers, rows) == ""


def test_infer_key_column_accepts_key_column():
    headers = ["RowKind", "Key"]
    rows = [
        {"RowKind": "Mission", "Key": "a"},
        {"RowKind": "Milestone", "Key": "b"},
    ]
    assert m.infer_key_column(headers, rows) == "Key"


def test_auto_map_matches_normalized():
    matched, utabs, ucsvs = m.auto_map_tabs_to_csvs(
        ["PlantData", "shop data"], ["PlantData.csv", "ShopData.csv", "Extra.csv"]
    )
    assert ("PlantData", "PlantData.csv") in matched
    assert ("shop data", "ShopData.csv") in matched
    assert ucsvs == ["Extra.csv"]
    assert utabs == []


def test_apply_column_map_passthrough():
    assert m.apply_column_map(["A", "B"], {}) == ["A", "B"]
    assert m.apply_column_map(["A"], {"A": "X"}) == ["X"]
