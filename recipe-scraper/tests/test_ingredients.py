import pytest

from recipe_scraper.ingredients import is_group_header, make_line, parse_line, parse_lines
from recipe_scraper.library import dish_name, ingredient_identity
from recipe_scraper.textutil import match_key, parse_minutes, stem


def one(text):
    lines = parse_line(text)
    assert len(lines) == 1, lines
    return lines[0]


@pytest.mark.parametrize("text,name,qty,unit,grams,ml", [
    ("ბანანი - 1 ცალი (დაახლოებით 100–120 გრ)", "ბანანი", 1, "pcs", 110, None),
    ("ქოქოსის ფანტელი - 60 გ", "ქოქოსის ფანტელი", 60, "g", 60, None),
    ("რძე - 300 მლ.", "რძე", 300, "ml", None, 300),
    ("არაჟანი - 1 სუფრის კოვზი ", "არაჟანი", 1, "tbsp", None, 15),
    ("1 ს/კ ტომატის პასტა", "ტომატის პასტა", 1, "tbsp", None, 15),
    ("25 გრ ქინძი", "ქინძი", 25, "g", 25, None),
    ("5 კბილი ნიორი", "ნიორი", 5, "clove", None, None),
    ("3 წითელი ბულგარული წიწაკა", "წითელი ბულგარული წიწაკა", 3, None, None, None),
    ("ფქვილი 500 გრ", "ფქვილი", 500, "g", 500, None),
    ("ფქვილი (500 გრ)", "ფქვილი", 500, "g", 500, None),
    ("ფქვილი: დაახლოებით 1 კგ", "ფქვილი", 1, "kg", 1000, None),
    ("1/2 ჭიქა შაქარი", "შაქარი", 0.5, "cup", None, 125),
    ("ნახევარი ჭიქა შაქარი", "შაქარი", 0.5, "cup", None, 125),
    ("1½ ჭიქა რძე", "რძე", 1.5, "cup", None, 375),
    ("250 გ სპაგეტი", "სპაგეტი", 250, "g", 250, None),
    ("1 ლ რძე", "რძე", 1, "l", None, 1000),
    ("3 ცხარე წიწაკა", "ცხარე წიწაკა", 3, None, None, None),  # "ც" must not be read as a unit here
    ("მარილი", "მარილი", None, None, None, None),
])
def test_parse_line(text, name, qty, unit, grams, ml):
    i = one(text)
    assert (i.name, i.quantity, i.unit_norm, i.grams, i.ml) == (name, qty, unit, grams, ml)


def test_ranges():
    for text in ("1-2 კბილი ნიორი", "1 - 2 კბილი ნიორი", "1 ან 2 კბილი ნიორი"):
        i = one(text)
        assert (i.name, i.quantity, i.quantity_max, i.unit_norm) == ("ნიორი", 1, 2, "clove")


def test_to_taste_and_purpose_notes():
    i = one("მარილი გემოვნებით")
    assert (i.name, i.note, i.optional) == ("მარილი", "გემოვნებით", True)
    i = one("მარილი - გემოვნებით")
    assert (i.name, i.optional) == ("მარილი", True)
    i = one("ზეთი შესაწვავად")
    assert (i.name, i.note, i.optional) == ("ზეთი", "შესაწვავად", False)


def test_alternatives_and_prep_notes():
    i = one("გუანჩალე ან პანჩეტა")
    assert (i.name, i.note) == ("გუანჩალე", "ან პანჩეტა")
    i = one("ქათმის ფილე, დაჭრილი")
    assert (i.name, i.note) == ("ქათმის ფილე", "დაჭრილი")


def test_amountless_lists_are_split():
    names = [i.name for i in parse_line("მწვანილი და ბროწეულის მარცვლები გასაფორმებლად")]
    assert names == ["მწვანილი", "ბროწეულის მარცვლები"]
    lines = parse_line("მარილი, პილპილი გემოვნებით")
    assert [i.name for i in lines] == ["მარილი", "პილპილი"] and all(i.optional for i in lines)


def test_word_numbers_need_word_boundary():
    assert one("ერთიანი ფქვილი").quantity is None


def test_groups():
    lines = parse_lines(["ცომისთვის:", "500 გრ ფქვილი", "შიგთავსისთვის", "300 გრ ყველი"])
    assert [(i.group, i.name) for i in lines] == [("ცომისთვის", "ფქვილი"), ("შიგთავსისთვის", "ყველი")]
    assert is_group_header("სოუსი:") and not is_group_header("ფქვილი: 500 გრ")


def test_structured_line():
    i = make_line("100 მლ/ლ ზეთი შესაწვავად", "ზეთი შესაწვავად", "100", "მლ/ლ")
    assert (i.name, i.unit_norm, i.ml, i.note) == ("ზეთი", "ml", 100, "შესაწვავად")


def test_stemmer_merges_case_forms():
    for a, b in [("ნიგოზი", "ნიგვზის"), ("ქათამი", "ქათმის"), ("პომიდორი", "პომიდვრის"), ("რძე", "რძის"),
                 ("სოკო", "სოკოს"), ("ბადრიჯანი", "ბადრიჯნით"), ("შაქარი", "შაქრის"), ("სალათა", "სალათის")]:
        assert stem(a) == stem(b), (a, b)
    assert match_key("ქათმის სალათა") == match_key("სალათა ქათმით")


def test_ingredient_identity_drops_prep_words_and_applies_aliases():
    aliases = {"ნიგვზის გული": "ნიგოზი"}
    assert ingredient_identity("დაკეპილი ნიგოზი", aliases)[0] == ingredient_identity("ნიგოზი", aliases)[0]
    assert ingredient_identity("ნიგვზის გული", aliases)[1] == "ნიგოზი"


@pytest.mark.parametrize("title,expected", [
    ("ქოქოსის და ბანანის ბურთულები - მხოლოდ 3 ინგრედიენტით!", "ქოქოსის და ბანანის ბურთულები"),
    ("ეს რეცეპტი მთელ ოჯახს აგაფრთოვანებს: უნაზესი კრეპები ქათმის შიგთავსით", "კრეპები ქათმის შიგთავსით"),
    ("ქლიავის ნამცხვარი, რომელიც პირში დნება - ყველაზე მარტივი დესერტი", "ქლიავის ნამცხვარი"),
    ("იტალიური პასტა კარბონარა (Classic Pasta Carbonara)", "იტალიური პასტა კარბონარა"),
    ("აჯაფსანდალის რეცეპტი", "აჯაფსანდალის"),
])
def test_dish_name(title, expected):
    assert dish_name(title) == expected


def test_parse_minutes():
    assert parse_minutes("0:45:00") == 45
    assert parse_minutes("1 სთ 30 წთ") == 90
    assert parse_minutes("PT1H10M") == 70
    assert parse_minutes("15 წუთი") == 15
    assert parse_minutes(20) == 20


def test_unit_without_number_means_one():
    i = one("მწიკვი მარილი")
    assert (i.name, i.quantity, i.unit_norm) == ("მარილი", 1, "pinch")
    i = one("ჭიქა შაქარი")
    assert (i.name, i.quantity, i.unit_norm, i.ml) == ("შაქარი", 1, "cup", 250)
    assert one("გ ფქვილი").unit_norm is None  # one-letter units never lead a line


def test_leading_approx_and_abbreviations():
    i = one("დაახლ. 500 გრ ფქვილი")
    assert (i.name, i.grams, i.note) == ("ფქვილი", 500, "დაახლ.")
    i = one("1 ს.კ. შაქარი")
    assert (i.name, i.unit_norm) == ("შაქარი", "tbsp")


def test_plausibility_filter():
    from recipe_scraper.ingredients import is_plausible
    assert is_plausible(one("1 ს.კ. შაქარი"))
    assert not is_plausible(one("2 გემოვნებით ავტ მაკა ჯღარკავა"))
    assert not is_plausible(one("2 გემოვნებით შეწვით გახურებულ ტაფაზე ვე მხრიდან. მზა ხაჭაპურებს კარაქით წაუსვათ."))


def test_names_ending_in_number_words_are_not_split():
    # "ნიორი" ends in "ორი" (two), "ხურმათი" in "ათი" (ten)
    for name in ("ნიორი", "ხურმათი"):
        i = one(name)
        assert (i.name, i.quantity) == (name, None)


def test_unit_abbreviation_slash_is_not_an_alternative():
    i = one("გემოვნებით 0.500 ჩ/კ დაფქული შავი პილპილი")
    assert (i.name, i.quantity, i.unit_norm, i.optional) == ("დაფქული შავი პილპილი", 0.5, "tsp", True)


def test_number_plus_to_taste_phrase_is_implausible():
    from recipe_scraper.ingredients import is_plausible
    assert not is_plausible(one("1 გემოვნებით ტაფის ხაჭაპური რომელსაც ჩვეულებრივისგან ვერ გამოარჩევ"))
    assert is_plausible(one("1 ჩ/კ მარილი გემოვნებით"))


def test_dish_names_back_to_nominative():
    from collections import Counter
    from recipe_scraper.library import is_genitive_recipe_title, nominative
    vocab = Counter({"სალათა": 3, "ჩიხირთმა": 1})
    assert is_genitive_recipe_title("ჩინური ტორტის რეცეპტი") and not is_genitive_recipe_title("ჩინური ტორტი")
    assert nominative("ჩინური ტორტის", vocab) == "ჩინური ტორტი"
    assert nominative("ქათმის სალათის", vocab) == "ქათმის სალათა"
    assert nominative("ჩიხირთმის", vocab) == "ჩიხირთმა"
    assert nominative("პესტოს", vocab) == "პესტო"
    assert dish_name("ფელამუში მარტივად და სწრაფად") == "ფელამუში"


def test_purpose_lines_are_ingredients_not_headings():
    assert not is_group_header("კარაქი ფორმისთვის")
    assert is_group_header("ცომისთვის") and is_group_header("შოკოლადის კრემისთვის")
    i = one("კარაქი ფორმისთვის")
    assert (i.name, i.note) == ("კარაქი", "ფორმისთვის")


def test_more_units():
    assert (one("1 ჩ/ჭ ნუტელა").unit_norm, one("1 ჩ/ჭ ნუტელა").ml) == ("cup", 250)
    assert one("2 ღერი ნიახური").unit_norm == "stalk"
    assert one("1 ტარო სიმინდი").unit_norm == "pcs"
