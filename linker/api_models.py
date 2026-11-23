"""The JSON API's response shapes (v1).

These models are the API contract: FastAPI checks every response against them and drops any field they don't list,
so a query can grow fields for the pages without the API changing. From 1.0.0 on, changes to v1 must be backward
compatible: add fields or routes, never remove, rename or retype one. tests/test_api_contract.py enforces that against
tests/openapi-v1.json.

Dates are ISO 8601 strings: a day ("2026-11-23") for monitor and download snapshots, a timestamp with its offset for
commits, issues and PRs.
"""

from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field


def _csv(value):
    """"a,b" (SQL group_concat) as ["a", "b"]."""
    return [v for v in value.split(",") if v] if isinstance(value, str) else value or []


def _source_link(key):
    """[origin_a, stable_a, origin_b, stable_b] as a SourcePair-shaped object."""
    if isinstance(key, (list, tuple)):
        return {"a": {"origin": key[0], "stable_id": key[1]}, "b": {"origin": key[2], "stable_id": key[3]}}
    return key


Csv = Annotated[list[str], BeforeValidator(_csv)]

STATUS = "Monitor verdict: pass, partial, fail, maybe, untestable or unsupported (null: not monitored)"
STATE = "GitHub state: OPEN, CLOSED (issues) or OPEN, CLOSED, MERGED (PRs)"
ORIGIN = "A repository, owner/repo (GET /api/v1/origins)"


class Error(BaseModel):
    detail: str | list[dict[str, Any]] = Field(examples=['issue not found'], description="What went wrong, in words; for an invalid parameter or body, "
                                                           "a list of {loc, msg, type} entries")


class Origin(BaseModel):
    id: str = Field(examples=['keiyoushi/extensions-source'], description=ORIGIN)
    kind: str = Field(examples=['keiyoushi-gradle'], description="Its layout: keiyoushi-gradle or haruneko-ts")
    repo_url: str = Field(examples=['https://github.com/keiyoushi/extensions-source'])
    default_branch: str = Field(examples=['main'])
    anchor_sha: str | None = Field(examples=['92906c0382547f30a03b3176f2f2429be6b54dce'], description="Commit the extractor last read")
    extensions: int = Field(examples=[1727], description="Current extensions")


# ---------------------------------------------------------------- tags

class PublicTag(BaseModel):
    name: str = Field(examples=['CF'], description="Use it in `tag=` and in tags/{tag}/{path}")
    labels: list[str] = Field(examples=[['Cloudflare protected', 'Cloudflare']], description="Issue labels that put this tag on the folders of an open issue")


class PrivateTag(BaseModel):
    name: str = Field(examples=['TODO'], description="Use it in `my_tag=` and in my-tags/{tag}/{path}")


class TagIssue(BaseModel):
    number: int = Field(examples=[21004])
    title: str = Field(examples=['MangaDex behind Cloudflare'])
    author: str | None = Field(examples=['alice'])
    created_at: str = Field(examples=['2026-11-02T10:00:00Z'])


class Tag(BaseModel):
    id: int = Field(examples=[1])
    label: str = Field(examples=['Is hostile'], description="The tag's name")
    color: str = Field(examples=['red'], description='A colour name, "" for none')
    scope: Literal["public", "private"] = Field(examples=['public'], description="private: the token's account's own tag")
    manual: bool = Field(examples=[True], description="Put on by hand (by_login, at); else only inherited from issues")
    by_login: str | None = Field(examples=['Starmania'])
    at: str | None = Field(examples=['2026-11-22T23:48:32+00:00'])
    issues: list[TagIssue] = Field(description="Open issues whose labels put this public tag on the folder")


class FacetValue(BaseModel):
    value: str = Field(examples=['en'])
    n: int = Field(examples=[484], description="Current extensions with this value")


class TagFacet(BaseModel):
    value: int = Field(examples=[1], description="The tag's id")
    label: str = Field(examples=['Is hostile'], description="The tag's name, for `tag=` or `my_tag=`")
    color: str = Field(examples=['red'])
    n: int = Field(examples=[3])


class TagFacets(BaseModel):
    public: list[TagFacet]
    private: list[TagFacet] | None = Field(None, description="Your private tags: only with a token")


class Facets(BaseModel):
    lang: list[FacetValue]
    theme: list[FacetValue]
    status: list[FacetValue]
    tags: TagFacets


# ---------------------------------------------------------------- extensions

class OpResult(BaseModel):
    op: str = Field(examples=['browse'], description="Monitor step: browse, latest, search, details, chapters, read, pages")
    label: str = Field(examples=['Browse'], description="The step in words")
    status: str = Field(examples=['pass'], description="pass, fail, maybe, skip, na, ...")
    status_label: str | None = Field(examples=['Works'], description="The result in words")
    error: str | None = Field(examples=[None])


class ExtensionItem(BaseModel):
    path: str = Field(examples=['src/all/mangadex'], description="The folder, in the repository's layout")
    lang: str | None = Field(examples=['all'])
    module: str | None = Field(examples=['mangadex'])
    name: str | None = Field(examples=['MangaDex'])
    package_name: str | None = Field(examples=['eu.kanade.tachiyomi.extension.all.mangadex'])
    theme: str | None = Field(examples=[None])
    content_warning: str | None = Field(examples=['SAFE'], description="SAFE, MIXED or NSFW")
    lib_version: str | None = Field(examples=['1.6'])
    version_code: int | None = Field(examples=[5])
    icon_url: str | None = Field(examples=['https://cdn.jsdelivr.net/gh/keiyoushi/extensions-source@main/src/all/mangadex/res/mipmap-xhdpi/ic_launcher.png'])
    removed: bool = Field(examples=[False])
    status: str | None = Field(examples=['pass'], description=STATUS)
    status_date: str | None = Field(examples=['2026-11-22'], description="Day of the latest monitor run")
    status_since: str | None = Field(examples=['2026-10-12'], description="Day the status started")
    status_days: int | None = Field(examples=[42])
    status_since_open: bool | None = Field(examples=[False], description="The status dates from the first monitor run, so it may be older")
    ops: list[OpResult] = Field(description="Per step results of the run that decided the status")
    rechecked: bool = Field(examples=[False], description="The status comes from a recheck through proxies")
    downloads_7d: int | None = Field(examples=[167595])
    downloads_all: int | None = Field(examples=[2128230])
    last_changed: str | None = Field(examples=['2026-11-20T00:36:58+01:00'], description="Date of the folder's last commit")
    open_issues: int = Field(examples=[1])
    pr_count: int = Field(examples=[4])
    open_prs: int = Field(examples=[0])
    hosts: list[str] = Field(examples=[['mangadex.org']], description="Current base domains")
    tags: list[Tag]


class ExtensionPage(BaseModel):
    total: int = Field(examples=[1727])
    limit: int = Field(examples=[60])
    offset: int = Field(examples=[0])
    items: list[ExtensionItem]


# ---------------------------------------------------------------- folders

class FolderDomain(BaseModel):
    host: str = Field(examples=['mangadex.org'])
    role: str = Field(examples=['base'], description="base, mirror or deeplink")
    from_sha: str = Field(examples=['92906c0382547f30a03b3176f2f2429be6b54dce'])
    from_date: str | None = Field(examples=['2026-10-16T21:28:13+05:00'])
    to_sha: str | None = Field(examples=[None], description="null: still declared")
    to_date: str | None = Field(examples=[None])


class FolderPr(BaseModel):
    number: int = Field(examples=[21303])
    title: str = Field(examples=['Core: add describe to store structured series details in memo'])
    state: str = Field(examples=['OPEN'], description=STATE)
    author: str | None = Field(examples=['hazptr'])
    created_at: str = Field(examples=['2026-11-21T16:15:36Z'])
    merged_at: str | None = Field(examples=[None])
    closed_at: str | None = Field(examples=[None])
    single_target: bool = Field(examples=[False], description="The PR changes this folder only")
    folder_count: int = Field(examples=[9])


class FolderIssue(BaseModel):
    number: int = Field(examples=[21246])
    title: str = Field(examples=['Kaguya 403 Forbbiden issue'])
    state: str = Field(examples=['CLOSED'], description=STATE)
    created_at: str = Field(examples=['2026-11-19T19:42:12Z'])
    closed_at: str | None = Field(examples=['2026-11-20T05:15:02Z'])
    author: str | None = Field(examples=['NaruHikaGame'])
    methods: Csv = Field(examples=[["form-name"]], description="How it was linked: title-name, form-name, domain, closing-pr, manual")
    confidence: float = Field(examples=[0.85])
    manual: bool = Field(examples=[False], description="Linked by hand")


class Commit(BaseModel):
    sha: str = Field(examples=['92906c0382547f30a03b3176f2f2429be6b54dce'])
    commit_date: str = Field(examples=['2026-11-22T18:37:48+05:00'])
    subject: str = Field(examples=['Core: add callback SMangaUpdate and migrate sources (#21272)'])
    body: str | None = Field(examples=['Add a callback `SMangaUpdate` that `KeiSource` flattens, and move sources onto it'])
    author: str | None = Field(examples=['NaruHikaGame'])
    pr_number: int | None = Field(examples=[21272])
    pr_title: str | None = Field(examples=['Core: add callback SMangaUpdate and migrate sources'])
    pr_merged_at: str | None = Field(examples=['2026-11-22T13:37:49Z'])
    pr_created_at: str | None = Field(examples=['2026-11-20T17:08:34Z'])


class Source(BaseModel):
    source_id: str = Field(examples=['4638673959522768501'])
    stable_id: str | None = Field(examples=['4638673959522768501'], description="The id source links use")
    name: str | None = Field(examples=['MangaDex'])
    lang: str | None = Field(examples=['all'])
    base_url: str | None = Field(examples=['https://mangadex.org'])
    url_type: str | None = Field(examples=['static'], description="static, custom or mirrors")
    mirror_urls: list[str] = Field(examples=[[]])


class RecheckTry(BaseModel):
    from_: str = Field(examples=['FR'], alias="from", serialization_alias="from", description="Proxy country")
    status: str = Field(examples=['maybe'], description="pass, fail, maybe or error")
    text: str = Field(examples=['turned away too (at Load pages)'], description="The outcome in words")
    ops: list[OpResult]


class Recheck(BaseModel):
    decided: bool = Field(examples=[True], description="The recheck set the status; else the sweep's verdict stands")
    verdict: str | None = Field(examples=['Still turned away through our proxies at Load pages'], description="In words")
    tries: list[RecheckTry]
    ops: list[OpResult] = Field(description="Steps of the deciding try")
    notes: list[str] = Field(examples=[['Cloudflare shows its checkbox: in the app, tick it in WebView']])


class Status(BaseModel):
    status: str = Field(examples=['pass'], description=STATUS)
    date: str = Field(examples=['2026-11-22'])
    since: str | None = Field(examples=['2026-10-12'])
    since_open: bool = Field(examples=[False], description="The status dates from the first monitor run, so it may be older")
    ops: list[OpResult]
    recheck: Recheck | None


class StatusPoint(BaseModel):
    date: str = Field(examples=['2026-11-22'])
    status: str = Field(examples=['pass'])


class Downloads(BaseModel):
    h1: int | None = Field(examples=[2563])
    h24: int | None = Field(examples=[62257])
    d7: int | None = Field(examples=[167595])
    d30: int | None = Field(examples=[847206])
    all_time: int | None = Field(examples=[2128230])
    date: str = Field(examples=['2026-11-22'])


class Side(BaseModel):
    origin: str = Field(examples=['manga-download/haruneko'])
    stable_id: str = Field(examples=['mangadex'])
    path: str | None = Field(examples=['web/src/engine/websites/MangaDex'])
    name: str | None = Field(examples=['MangaDex'])
    lang: str | None = Field(examples=['all'])
    hosts: list[str] = Field(examples=[['mangadex.org']], description="Current hosts")


class SourceRef(BaseModel):
    origin: str = Field(examples=['keiyoushi/extensions-source'], description=ORIGIN)
    stable_id: str = Field(examples=['6404943692147160087'], description="A source's stable_id (folder `sources`)")


class SourcePair(BaseModel):
    a: SourceRef
    b: SourceRef


SourceLinks = Annotated[list[SourcePair], BeforeValidator(lambda keys: [_source_link(k) for k in keys or []])]
LINK_STATE = "same (a host in common), drift (none in common) or gone (a side was removed)"
BEHIND = "The repo that probably has the old domain (null: unknown)"
BASIS = "Why: left (it still has a host the other side left) or older (it adopted its host first)"


class LinkPair(BaseModel):
    me: Side
    other: Side
    host: str | None = Field(examples=['mangadex.org'], description="The host that linked them")
    since: str | None = Field(examples=['2026-11-08T13:27:22+05:00'])
    manual: bool = Field(examples=[False])
    by_login: str | None = Field(examples=[None])
    state: str = Field(examples=['same'], description=LINK_STATE)
    behind: str | None = Field(examples=[None], description=BEHIND)
    basis: str | None = Field(examples=[None], description=BASIS)
    source_link: Annotated[SourcePair, BeforeValidator(_source_link)] = Field(
        validation_alias=AliasChoices("source_link", "key"), description="The body for /api/v1/source-links")


class LinkStatus(BaseModel):
    status: str = Field(examples=['pass'], description=STATUS)
    since: str | None = Field(examples=['2026-10-12'])


class ElsewhereFolder(BaseModel):
    path: str = Field(examples=['web/src/engine/websites/MangaDex'])
    name: str | None = Field(examples=['MangaDex'])
    state: str = Field(examples=['same'], description="The worst of its pairs: " + LINK_STATE)
    behind: str | None = Field(examples=[None], description=BEHIND)
    basis: str | None = Field(examples=[None], description=BASIS)
    manual: bool = Field(examples=[False])
    by_login: str | None = Field(examples=[None])
    hosts: list[str] = Field(examples=[['mangadex.org']])
    icon_url: str | None = Field(examples=['https://cdn.jsdelivr.net/gh/manga-download/haruneko@master/web/src/engine/websites/MangaDex.webp'])
    removed: bool = Field(examples=[False])
    open_issues: int = Field(examples=[0])
    status: LinkStatus | None
    pairs: list[LinkPair] = Field(description="One per linked source")


class Elsewhere(BaseModel):
    links: dict[str, list[ElsewhereFolder]] = Field(description="Linked folders, by other repository")
    hidden: list[LinkPair] = Field(description="Automatic links an editor hid")


class ThemeFolder(BaseModel):
    path: str = Field(examples=['lib-multisrc/madara'])
    history: list[Commit]
    issues: list[FolderIssue]
    domains: list[FolderDomain] = Field(description="Current domains only")


class ThemeExtension(BaseModel):
    path: str = Field(examples=['src/en/manhwatop'])
    name: str | None = Field(examples=['Manhwatop'])
    lang: str | None = Field(examples=['en'])
    removed: bool = Field(examples=[False])
    status: str | None = Field(examples=['pass'], description=STATUS)


class Folder(BaseModel):
    """An extension (kind src) or theme folder. Fields marked "src" or "theme" are present for that kind only."""
    origin: str = Field(examples=['keiyoushi/extensions-source'])
    path: str = Field(examples=['src/all/mangadex'])
    kind: str = Field(examples=['src'], description="src (an extension) or theme")
    lang: str | None = Field(examples=['all'])
    module: str | None = Field(examples=['mangadex'])
    name: str | None = Field(examples=['MangaDex'])
    package_name: str | None = Field(examples=['eu.kanade.tachiyomi.extension.all.mangadex'])
    theme: str | None = Field(examples=[None])
    theme_path: str | None = Field(examples=[None])
    content_warning: str | None = Field(examples=['SAFE'], description="SAFE, MIXED or NSFW")
    lib_version: str | None = Field(examples=['1.6'])
    version_code: int | None = Field(examples=[5])
    icon_url: str | None = Field(examples=['https://cdn.jsdelivr.net/gh/keiyoushi/extensions-source@main/src/all/mangadex/res/mipmap-xhdpi/ic_launcher.png'])
    source_url: str | None = Field(examples=['https://github.com/keiyoushi/extensions-source/tree/main/src/all/mangadex'])
    first_sha: str | None = Field(examples=['7363e4298c2a6cc4e32e6ef6af2d48611d5b619e'])
    last_sha: str | None = Field(examples=['92906c0382547f30a03b3176f2f2429be6b54dce'])
    removed_sha: str | None = Field(examples=[None], description="null: not removed")
    first_date: str | None = Field(examples=['2024-01-06T17:53:13+00:00'])
    last_date: str | None = Field(examples=['2026-11-22T18:37:48+05:00'])
    domains: list[FolderDomain]
    prs: list[FolderPr]
    issues: list[FolderIssue]
    history: list[Commit]
    tags: list[Tag] = Field(description="Public and inherited tags, plus your private ones with a token")
    sources: list[Source] | None = Field(None, description="src")
    apk_url: str | None = Field(None, examples=['https://github.com/keiyoushi/extensions/releases/download/b4f9106-0/tachiyomi-all.mangadex-v1.6.5.apk'], description="src")
    status: Status | None = Field(None, description="src; null: not monitored")
    status_history: list[StatusPoint] | None = Field(None, description="src; the last 60 runs, oldest first")
    downloads: Downloads | None = Field(None, description="src; null: not tracked")
    elsewhere: Elsewhere | None = Field(None, description="src; links to other repositories")
    theme_folder: ThemeFolder | None = Field(None, description="src on a theme: the theme's own history, issues and domains")
    extensions: list[ThemeExtension] | None = Field(None, description="theme: the extensions built on it")


# ---------------------------------------------------------------- issues and PRs

class IssueLink(BaseModel):
    path: str = Field(examples=['src/id/kaguya'])
    kind: str | None = Field(examples=['src'])
    name: str | None = Field(examples=['Kaguya'])
    lang: str | None = Field(examples=['id'])
    theme: str | None = Field(examples=[None])
    theme_path: str | None = Field(examples=[None])
    methods: Csv = Field(examples=[['closing-pr']], description="title-name, form-name, domain, closing-pr, manual")
    confidence: float = Field(examples=[0.8])
    manual: bool = Field(examples=[False])
    by_login: str | None = Field(examples=[None], description="Who linked it by hand")


class RemovedLink(BaseModel):
    path: str = Field(examples=['src/id/kaguyaa'])
    at: str = Field(examples=['2026-11-22T23:48:32+00:00'])
    by_login: str | None = Field(examples=['Starmania'])


class IssuePr(BaseModel):
    number: int = Field(examples=[21247])
    title: str = Field(examples=['Kaguya: fix 403'])
    state: str = Field(examples=['MERGED'], description=STATE)
    merged_at: str | None = Field(examples=['2026-11-20T05:15:01Z'])


class Issue(BaseModel):
    origin: str = Field(examples=['keiyoushi/extensions-source'])
    number: int = Field(examples=[21246])
    title: str = Field(examples=['Kaguya 403 Forbbiden issue'])
    state: str = Field(examples=['CLOSED'], description=STATE)
    author: str | None = Field(examples=['NaruHikaGame'])
    created_at: str = Field(examples=['2026-11-19T19:42:12Z'])
    closed_at: str | None = Field(examples=['2026-11-20T05:15:02Z'])
    body: str | None = Field(examples=['Kaguya gives 403 Forbidden when browsing'])
    labels: list[str] = Field(examples=[['Bug']])
    links: list[IssueLink] = Field(description="Folders it is about")
    removed_links: list[RemovedLink] = Field(description="Automatic links an editor hid")
    prs: list[IssuePr] = Field(description="PRs that close it")


class FolderName(BaseModel):
    path: str = Field(examples=['src/en/hiperdex'])
    kind: str | None = Field(examples=['src'])
    name: str | None = Field(examples=['Hiperdex'])
    lang: str | None = Field(examples=['en'])
    theme: str | None = Field(examples=['hiper'])
    theme_path: str | None = Field(examples=['lib-multisrc/hiper'])


class Ref(BaseModel):
    number: int = Field(examples=[21246])
    title: str = Field(examples=['Kaguya 403 Forbbiden issue'])
    state: str = Field(examples=['CLOSED'], description=STATE)


class PrCommit(BaseModel):
    sha: str = Field(examples=['92906c0382547f30a03b3176f2f2429be6b54dce'])
    commit_date: str = Field(examples=['2026-02-15T08:16:27+00:00'], validation_alias=AliasChoices("commit_date", "date"))
    subject: str = Field(examples=['Update Hiperdex DateFormat (#13359)'])
    body: str | None = Field(examples=[None])


class PullRequest(BaseModel):
    origin: str = Field(examples=['keiyoushi/extensions-source'])
    number: int = Field(examples=[13359])
    title: str = Field(examples=['Update Hiperdex DateFormat'])
    state: str = Field(examples=['MERGED'], description=STATE)
    author: str | None = Field(examples=['KurtzPT'])
    created_at: str = Field(examples=['2026-02-14T01:37:44Z'])
    merged_at: str | None = Field(examples=['2026-02-15T08:16:27Z'])
    closed_at: str | None = Field(examples=['2026-02-15T08:16:27Z'])
    body: str | None = Field(examples=['Fixes the chapter dates'])
    single_target: str | None = Field(examples=['src/en/hiperdex'], description="The one folder it changes, if it changes exactly one")
    folders: list[FolderName]
    issues: list[Ref] = Field(description="Issues it closes")
    commits: list[PrCommit] = Field(description="Its commits on the default branch")


# ---------------------------------------------------------------- domains and search

class DomainItem(BaseModel):
    host: str = Field(examples=['mangadex.org'])
    current_extensions: int = Field(examples=[1])
    all_extensions: int = Field(examples=[2])
    roles: Csv = Field(examples=[["base"]], description="base, mirror, deeplink")


class DomainPage(BaseModel):
    total: int = Field(examples=[3212])
    limit: int = Field(examples=[200])
    offset: int = Field(examples=[0])
    items: list[DomainItem]


class DomainUsage(FolderDomain):
    path: str = Field(examples=['src/all/mangadex'])
    name: str | None = Field(examples=['MangaDex'])
    lang: str | None = Field(examples=['all'])
    kind: str | None = Field(examples=['src'])
    removed: bool = Field(examples=[False])


class Domain(BaseModel):
    host: str = Field(examples=['mangadex.org'])
    usages: list[DomainUsage] = Field(description="Current first, subdomains included")


class Search(BaseModel):
    query: str = Field(examples=['mangadex'])
    issue: Ref | None = Field(examples=[None], description="For #123 or 123")
    pr: Ref | None = Field(examples=[None])
    domains: list[DomainItem] = Field(description="The first 100")
    domains_total: int = Field(examples=[3])
    extensions: list[ExtensionItem] = Field(description="The first 100, removed ones included")
    extensions_total: int = Field(examples=[2])


# ---------------------------------------------------------------- cross-repo

class DriftIgnored(BaseModel):
    reason: str = Field(examples=['redirect'], description="right_side, redirect or other")
    label: str = Field(examples=['Low priority: the old domain redirects'], description="The reason in words")
    by_login: str | None = Field(examples=['Starmania'])
    at: str = Field(examples=['2026-11-22T23:48:32+00:00'])


class DriftRow(BaseModel):
    a: Side
    b: Side
    behind: str | None = Field(examples=['keiyoushi/extensions-source'], description=BEHIND)
    basis: str | None = Field(examples=['left'], description=BASIS)
    host: str | None = Field(examples=['dualeotruyenvie.com'], description="The host that once linked them")
    since: str | None = Field(examples=['2026-10-16T21:28:13+05:00'])
    langs: list[str] = Field(examples=[['vi']])
    source_links: SourceLinks = Field(validation_alias=AliasChoices("source_links", "keys"),
                                      description="The linked source pairs, each a body for /api/v1/source-links")
    pair: list[str] = Field(examples=[['keiyoushi/extensions-source', 'src/vi/dualeotruyen', 'manga-download/haruneko', 'web/src/engine/websites/DuaLeoTruyen']], description="[origin_a, path_a, origin_b, path_b]: the body's pair for /api/v1/drift/ignored")
    ignored: DriftIgnored | None


class DriftCounts(BaseModel):
    behind: dict[str, int] = Field(examples=[{'keiyoushi/extensions-source': 7, 'manga-download/haruneko': 4}], description='Rows not ignored, by the repo behind ("unknown" when not known)')
    ignored: int = Field(examples=[0])


class Drift(BaseModel):
    total: int = Field(examples=[11], description="Rows not ignored")
    counts: DriftCounts
    items: list[DriftRow]


class Tracked(BaseModel):
    status: bool = Field(examples=[True], description="The monitor covers this repository")
    downloads: bool = Field(examples=[True], description="Download counts exist for it")


class NamedFolder(BaseModel):
    path: str = Field(examples=['src/tr/dxdfansub'])
    name: str | None = Field(examples=['DxD Fansub'])


class SiteRequest(BaseModel):
    number: int = Field(examples=[21004])
    title: str = Field(examples=['New source'])
    state: str = Field(examples=['CLOSED'], description=STATE)
    removed: bool = Field(examples=[False], description='Labelled "Previously deleted": the repo dropped the site on purpose')


class MissingItem(BaseModel):
    path: str = Field(examples=['web/src/engine/websites/DxdFansub'])
    name: str | None = Field(examples=['DxD Fansub'])
    lang: str | None = Field(examples=['tr'])
    icon_url: str | None = Field(examples=['https://cdn.jsdelivr.net/gh/manga-download/haruneko@master/web/src/engine/websites/DxdFansub.webp'])
    content_warning: str | None = Field(examples=[None])
    status: str | None = Field(examples=[None], description=STATUS)
    status_since: str | None = Field(examples=[None])
    downloads_7d: int | None = Field(examples=[None])
    hosts: list[str] = Field(examples=[['dxdfansub.com']])
    removed_in_target: list[NamedFolder] = Field(description="Linked folders of the target that were removed")
    same_name: NamedFolder | None = Field(examples=[None], description="An unlinked target folder with the same name: maybe the same site")
    requests: list[SiteRequest] = Field(description="The target's site-request issues naming one of its domains")


class MissingOrigin(BaseModel):
    tracked: Tracked
    items: list[MissingItem]


class Missing(BaseModel):
    target: str = Field(examples=['keiyoushi/extensions-source'])
    origins: dict[str, MissingOrigin] = Field(description="Sites the target lacks, by repository that has them")
    total: int = Field(examples=[107])


# ---------------------------------------------------------------- writes

class SourceLinkResult(SourcePair):
    override: Literal["add", "remove"] | None = Field(examples=['add'], description="The manual decision now in place (null: automatic)")


DRIFT_REASON = Literal["right_side", "redirect", "other"]


class DriftIgnore(BaseModel):
    pair: list[str] = Field(examples=[['keiyoushi/extensions-source', 'src/vi/dualeotruyen', 'manga-download/haruneko', 'web/src/engine/websites/DuaLeoTruyen']], description="The row's `pair` from GET /api/v1/drift: [origin_a, path_a, origin_b, path_b]",
                            min_length=4, max_length=4)
    reason: DRIFT_REASON | None = Field(
        None, description="PUT only: right_side (\"behind\" has the better domain), redirect (the old domain redirects), "
                          "other (not worth fixing)")


class DriftIgnoreResult(BaseModel):
    pair: list[str] = Field(examples=[['keiyoushi/extensions-source', 'src/vi/dualeotruyen', 'manga-download/haruneko', 'web/src/engine/websites/DuaLeoTruyen']])
    reason: DRIFT_REASON | None = Field(examples=['redirect'], description="null: back in the list")
