import collections
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u9fff]")
DISPLAY_FIELDS = ("kind", "category", "grade", "required", "spec", "use", "part_no", "note")


def display_strings():
    """Read only the public index and the board documents it actually lists."""
    result = collections.defaultdict(set)
    index = json.loads((ROOT / "data/index.json").read_text(encoding="utf-8"))
    for entry in index:
        for field in ("title", "subtitle"):
            result[field].add(entry[field])
        document = json.loads((ROOT / "data" / (entry["id"] + ".json")).read_text(encoding="utf-8"))
        for field in ("title", "subtitle"):
            result[field].add(document[field])
        for group in document["groups"]:
            for field in DISPLAY_FIELDS:
                if isinstance(group.get(field), str):
                    result[field].add(group[field])
            for part in group["parts"]:
                for field in ("use", "kind"):
                    if isinstance(part.get(field), str):
                        result[field].add(part[field])
    return {field: {value for value in values if CJK.search(value)}
            for field, values in result.items()}


def load_dictionary():
    asset = (ROOT / "i18n-en.js").read_text(encoding="utf-8")
    match = re.fullmatch(r"\s*window\.PK_ASSEMBLY_EN_DATA\s*=\s*(\{.*\})\s*;\s*", asset, re.S)
    if not match:
        raise AssertionError("Engineering translations must be a plain JSON assignment")
    return json.loads(match.group(1), object_pairs_hook=unique_object)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError("Duplicate translation key: " + key)
        result[key] = value
    return result


def technical_tokens(text):
    """Keep exact model/reference/value tokens, including pin numbers and units."""
    words = re.findall(r"[A-Za-z0-9]+(?:[_.-][A-Za-z0-9]+)*", text)
    return {word for word in words
            if any(char.isdigit() for char in word) or (word.isupper() and len(word) > 1)}


def validate_dictionary(mapping, fields=None):
    fields = display_strings() if fields is None else fields
    sources = set().union(*fields.values())
    missing = sources - mapping.keys()
    if missing:
        raise AssertionError("Missing English translations: " + repr(sorted(missing)))
    for source in sources:
        english = mapping[source]
        if not isinstance(english, str) or not english.strip() or CJK.search(english):
            raise AssertionError("Incomplete English translation: " + source)
        lost = {token for token in technical_tokens(source)
                if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", english)}
        if lost:
            raise AssertionError("Lost technical tokens " + repr(sorted(lost)) + ": " + source)
        # These assertions protect the direction of the highest-risk assembly rules.
        if "R7/R8 绝不能贴" in source:
            if not re.search(r"R7/R8 MUST NOT be fitted", english):
                raise AssertionError("Power variant R7/R8 prohibition reversed")
            if not re.search(r"damage the board", english):
                raise AssertionError("Board damage warning omitted")
        if "R7/R8 必贴" in source and "R7/R8 MUST be fitted" not in english:
            raise AssertionError("No-power variant R7/R8 requirement omitted")
        if "带电源版不贴；不带电源版必须贴" in source:
            if "with power circuitry: do not fit" not in english or "without power circuitry: MUST fit" not in english:
                raise AssertionError("Mixed-variant R7/R8 warning reversed")
        if "先切断对应铜桥" in source:
            if "cut the corresponding copper bridge first" not in english:
                raise AssertionError("Copper bridge cutting order omitted")
        if "只能用 ML/MS" in source and "use ONLY ML/MS" not in english:
            raise AssertionError("Backup battery chemistry restriction omitted")
        if "禁止用 0Ω 直连 3V3_RF" in source and "MUST NOT connect directly to 3V3_RF with 0Ω" not in english:
            raise AssertionError("LNA bias prohibition omitted")
        if "双向、无极性" in source and "bidirectional, non-polarized" not in english:
            raise AssertionError("ESD polarity information omitted")
        if "不贴任何件" in source and "DO NOT fit any components" not in english:
            raise AssertionError("Bare-pad assembly prohibition omitted")
        correction = re.search(r"料号核对：`([^`]+)`.*正确应为 `([^`]+)`", source)
        if correction:
            wrong, correct = correction.groups()
            if not english.startswith("Part-number check: `" + wrong + "`") or "the correct number is `" + correct + "`" not in english:
                raise AssertionError("Part-number correction direction changed")


class EngineeringEnglishTests(unittest.TestCase):
    def test_all_current_public_display_fields_have_complete_english(self):
        validate_dictionary(load_dictionary())

    def test_removed_real_entry_is_detected(self):
        mapping = load_dictionary()
        source = next(source for source in display_strings()["subtitle"] if "先切断对应铜桥" in source)
        del mapping[source]
        with self.assertRaisesRegex(AssertionError, "Missing English"):
            validate_dictionary(mapping)

    def test_reversed_real_assembly_warning_is_detected(self):
        mapping = load_dictionary()
        sources = [source for source in display_strings()["subtitle"] if "R7/R8 绝不能贴" in source]
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source):
                candidate = dict(mapping)
                self.assertIn("R7/R8 MUST NOT be fitted", candidate[source])
                candidate[source] = candidate[source].replace("R7/R8 MUST NOT be fitted", "R7/R8 MUST be fitted")
                with self.assertRaisesRegex(AssertionError, "prohibition reversed"):
                    validate_dictionary(candidate)

    def test_deleted_real_technical_token_is_detected(self):
        mapping = load_dictionary()
        source = next(source for source in display_strings()["note"] if "禁止用 0Ω 直连 3V3_RF" in source)
        self.assertEqual(mapping[source].count("3V3_RF"), 1)
        mapping[source] = mapping[source].replace("3V3_RF", "the supply")
        with self.assertRaisesRegex(AssertionError, "Lost technical tokens"):
            validate_dictionary(mapping)

    def test_reversed_real_required_fitting_is_detected(self):
        mapping = load_dictionary()
        sources = [source for source in display_strings()["subtitle"] if "R7/R8 必贴" in source]
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source):
                candidate = dict(mapping)
                self.assertIn("R7/R8 MUST be fitted", candidate[source])
                candidate[source] = candidate[source].replace("R7/R8 MUST be fitted", "R7/R8 MUST NOT be fitted")
                with self.assertRaisesRegex(AssertionError, "requirement omitted"):
                    validate_dictionary(candidate)

    def test_reversed_real_part_corrections_are_detected(self):
        mapping = load_dictionary()
        sources = [source for source in display_strings()["note"] if "料号核对：" in source]
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source):
                wrong, correct = re.search(r"料号核对：`([^`]+)`.*正确应为 `([^`]+)`", source).groups()
                candidate = dict(mapping)
                self.assertEqual(candidate[source].count("the correct number is `" + correct + "`"), 1)
                candidate[source] = candidate[source].replace("the correct number is `" + correct + "`",
                                                              "the correct number is `" + wrong + "`")
                # Retain both tokens so only the semantic direction can detect it.
                candidate[source] += " (rejected alternative `" + correct + "`)"
                with self.assertRaisesRegex(AssertionError, "correction direction"):
                    validate_dictionary(candidate)

    def test_changed_real_bridge_sequence_is_detected(self):
        mapping = load_dictionary()
        sources = [source for values in display_strings().values() for source in values
                   if "先切断对应铜桥" in source]
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source):
                candidate = dict(mapping)
                candidate[source] = candidate[source].replace("cut the corresponding copper bridge first",
                                                              "leave the corresponding copper bridge intact")
                with self.assertRaisesRegex(AssertionError, "cutting order"):
                    validate_dictionary(candidate)


if __name__ == "__main__":
    unittest.main()
