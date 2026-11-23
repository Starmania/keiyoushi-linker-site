from linker.issue_body import clean, render

FORM = """### Source information

ManhwaLand.mom version 1.4.39

### Other details

_No response_

### Acknowledgements

- [x] I have updated the extension to the latest version.
- [x] I have written a short but informative title.

### <!-- footer -->

---

Add a :+1: [reaction] to issues.
"""


def test_form_boilerplate_is_dropped():
    assert clean(FORM) == "### Source information\n\nManhwaLand.mom version 1.4.39"
    assert clean("Free text, no form.\n\n- [x] done") == "Free text, no form.\n\n- [x] done"  # tasks with prose stay
    assert clean(None) == ""


def test_render_escapes_html_and_links_urls():
    html = render("<script>alert(1)</script> see https://example.org and [x](javascript:alert(1))")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a rel="nofollow noopener" href="https://example.org">' in html
    assert 'href="javascript' not in html


def test_struck_urls_and_github_images():
    html = render('~~https://old.example/~~ https://new.example/\n\n<img width="1080" alt="Image" src="https://github.com/user-attachments/assets/1b37" />')
    assert '<s><a rel="nofollow noopener" href="https://old.example/">' in html
    assert '<img loading="lazy" src="https://github.com/user-attachments/assets/1b37" alt="Image"' in html
    assert 'src="javascript' not in render('<img src="javascript:x">')  # only https images are kept
