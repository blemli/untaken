"""every checker against its live site: the known name must be taken, a random one free.  uv pip install pytest && pytest"""
import concurrent.futures, pathlib, re, pytest, yaml, check

LIVE = [k for k, s in check.SITES.items() if not s.get("manual")]


@pytest.fixture(scope="session")
def verdicts():  # all sites at once, the tests below only read
    with concurrent.futures.ThreadPoolExecutor(24) as pool:
        return dict(zip(LIVE, pool.map(lambda k: (check.probe(k, str(check.SITES[k]["known"])), check.probe(k, check.nobody(k))), LIVE)))


def test_shape():
    assert len(check.SITES) <= 200, "the grid holds 200 sites"
    for key, s in check.SITES.items():
        assert re.fullmatch("[a-z0-9]+", key) and s.get("signup") and re.compile(s.get("valid", "")) and re.compile(f"[{s.get('chars', check.CHARS)}]"), key
        if s.get("manual"): assert "link" in s, key; continue
        assert any(t in s for t in check.TRANSPORTS) and not check.why(key, str(s["known"])) and check.nobody(key), key
        assert all(s[v] and set(s[v]) <= set(check.STRATEGIES) for v in check.VERDICTS if v in s), f"{key}: empty rule or unknown strategy"
        assert {"taken", "probably_taken"} & set(s) and {"free", "probably_free"} & set(s), f"{key}: needs evidence for both sides"


def test_presets():
    for name, preset in yaml.safe_load(pathlib.Path(check.__file__).with_name("presets.yaml").read_text()).items():
        assert set(preset["sites"]) <= set(check.SITES), name


@pytest.mark.parametrize("key", LIVE)
def test_known_is_taken(key, verdicts): assert verdicts[key][0].endswith("taken")


@pytest.mark.parametrize("key", LIVE)
def test_nobody_is_free(key, verdicts): assert verdicts[key][1].endswith("free")
