import json
from pathlib import Path

from recipe_scraper.nutrition import parse_fiber_table, parse_gemrielia_bundle
from recipe_scraper.sites import SITES, get_site
from recipe_scraper.sites.gemrielia import ingredient_texts

FX = Path(__file__).parent / "fixtures"


def parse(site, url, fixture):
    return SITES[site].parse(url, (FX / fixture).read_text(encoding="utf-8"))


def names(rec):
    return [i.name for i in rec.ingredients]


def test_kulinaria():
    r = parse("kulinaria", "https://kulinaria.ge/receptebi/ბადრიჯანი-ნიგვზით_116/", "kulinaria.html")
    assert r.title == "ბადრიჯანი ნიგვზით" and r.source_id == "116"
    assert r.categories == ["ქართული სამზარეულო", "რეგიონული კულინარია"]
    assert (r.total_minutes, r.servings, r.author) == (45, 14, "tamta")
    assert names(r)[:4] == ["ბადრიჯანი", "ნიგოზი", "ნიორი", "უცხო სუნელი"]
    first = r.ingredients[0]
    assert (first.quantity, first.unit, first.unit_norm, first.grams) == (500, "გრამი", "g", 500)
    oil = next(i for i in r.ingredients if i.name == "ზეთი")
    assert (oil.unit_norm, oil.ml, oil.note) == ("ml", 100, "შესაწვავად")
    assert len(r.ingredients) == 13 and len(r.steps) == 4


def test_samzareulo():
    r = parse("samzareulo", "https://samzareulo.net/receptebi/223-imeruli-xachapuri.html", "samzareulo.html")
    assert r.title == "იმერული ხაჭაპური რძის ცომით" and r.source_id == "223"
    assert names(r) == ["რძე", "არაჟანი", "კვერცხი", "კარაქი", "შაქარი", "საფუარი", "მარილი", "ფქვილი",
                        "სულგუნი", "იმერული ყველი"]
    assert (r.ingredients[0].quantity, r.ingredients[0].unit_norm) == (300, "ml")
    assert len(r.steps) == 2 and r.steps[0].startswith("თბილ რძეში")
    assert "იმერული ხაჭაპური" in r.tags


def test_kerdzebi():
    r = parse("kerdzebi", "https://kerdzebi.ge/recepti/pasta-carbonara-1784725429472", "kerdzebi.html")
    assert r.title.startswith("იტალიური პასტა კარბონარა")
    assert names(r) == ["სპაგეტი", "გუანჩალე", "კვერცხის გული", "პეკორინო რომანო", "ახლად დაფქული შავი პილპილი"]
    assert (r.total_minutes, r.servings, r.calories) == (25, 2, 480)
    assert len(r.steps) == 4 and r.categories == ["ცომეული & ხაჭაპური"]


def test_fiber():
    r = parse("fiber", "https://fiber.ge/recepti/ajapsandali/", "fiber.html")
    assert r.title == "აჯაფსანდალის რეცეპტი" and r.categories == ["კერძები"]
    assert (r.total_minutes, r.servings, r.calories) == (60, 4, 110)
    assert len(r.ingredients) == 9 and names(r)[-1] == "ნიორი"
    assert len(r.steps) == 5


def test_gemrielia_api_payload():
    data = json.loads((FX / "gemrielia_api.json").read_text(encoding="utf-8"))["result"]
    r = SITES["gemrielia"].from_api("https://gemrielia.ge/recipe/9724-kokosis-da-bananis-burtulebi-mxolod-3-ingredientit/", data)
    assert names(r) == ["ბანანი", "ქოქოსის ფანტელი", "კაკაო"]
    assert r.ingredients[0].grams == 110
    assert (r.categories, r.total_minutes, r.servings, r.extra["difficulty"]) == (["დესერტები"], 10, 12, "მარტივი")
    assert len(r.steps) == 2 and r.image.startswith("https://gemrielia.ge/media/")


def test_gemrielia_rendered_page():
    r = parse("gemrielia", "https://gemrielia.ge/recipe/9724-kokosis/", "gemrielia_rendered.html")
    assert r.title == "ქოქოსის და ბანანის ბურთულები - მხოლოდ 3 ინგრედიენტით!"
    assert names(r) == ["ბანანი", "ქოქოსის ფანტელი", "კაკაო"]
    assert len(r.steps) == 1


def test_gemrielia_ingredient_shapes():
    assert ingredient_texts("ბანანი - 1 ცალი\nკაკაო - 30 გ") == ["ბანანი - 1 ცალი", "კაკაო - 30 გ"]
    assert ingredient_texts([{"name": "ბანანი", "amount": "1", "unit": "ცალი"}]) == ["ბანანი - 1 ცალი"]
    assert ingredient_texts([{"title": "ცომისთვის", "items": ["ფქვილი - 500 გ"]}]) == ["ცომისთვის:", "ფქვილი - 500 გ"]
    editorjs = {"blocks": [{"type": "header", "data": {"text": "სოუსი"}},
                           {"type": "list", "data": {"style": "unordered", "items": ["არაჟანი - 200 გ"]}}]}
    assert ingredient_texts(json.dumps(editorjs)) == ["სოუსი:", "არაჟანი - 200 გ"]


def test_nutrition_tables():
    g = parse_gemrielia_bundle((FX / "gemrielia_bundle.js").read_text(encoding="utf-8"))
    banana = next(r for r in g if r["name"] == "ბანანი")
    assert (banana["kcal"], banana["carbs"], banana["group"]) == (89, 21, "ხილი და ბაღჩეული")
    f = parse_fiber_table((FX / "fiber_calories.html").read_text(encoding="utf-8"))
    assert len(f) == 5
    garlic = next(r for r in f if r["name"] == "ნიორი")
    assert (garlic["kcal"], garlic["protein"], garlic["carbs"], garlic["fat"], garlic["group"]) == \
        (149, 6.4, 33, 0.5, "ბოსტნეულის კალორიები")


def test_generic_site_spec():
    s = get_site("sakhluri=https://sakhluri.ge|/recipe/")
    assert s.name == "sakhluri" and s.recipe_re.search("https://sakhluri.ge/recipe/x")


def test_kulinaria_unit_led_line():
    r = parse("kulinaria", "https://kulinaria.ge/receptebi/შოკოლადის-მაფინი_1088/", "kulinaria_muffin.html")
    assert r.title == "შოკოლადის მაფინი" and len(r.ingredients) == 10
    salt = r.ingredients[-1]
    assert (salt.name, salt.quantity, salt.unit_norm) == ("მარილი", 1, "pinch")


def test_fiber_fractions():
    r = parse("fiber", "https://fiber.ge/recepti/adjaruli-khachapuri/", "fiber_khachapuri.html")
    milk = r.ingredients[0]
    assert (milk.name, milk.quantity, milk.unit_norm) == ("რძე", 1.25, "cup")
    assert (r.total_minutes, r.calories) == (190, 715)


def test_kerdzebi_junk_lines_are_detectable():
    from recipe_scraper.ingredients import is_plausible
    r = parse("kerdzebi", "https://kerdzebi.ge/recepti/tapis-khachapuri", "kerdzebi_khachapuri.html")
    kept = [i.name for i in r.ingredients if is_plausible(i)]
    assert "კეფირი" in kept and "ფქვილი" in kept
    assert not any("ჯღარკავა" in n or "წაუსვათ" in n for n in kept)
