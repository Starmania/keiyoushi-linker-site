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
    detail: str | list[dict[str, Any]] = Field(description="What went wrong, in words; for an invalid parameter or body, "
                                                           "a list of {loc, msg, type} entries")


class Origin(BaseModel):
    id: str = Field(description=ORIGIN)
    kind: str = Field(description="Its layout: keiyoushi-gradle or haruneko-ts")
    repo_url: str
    default_branch: str
    anchor_sha: str | None = Field(description="Commit the extractor last read")
    extensions: int = Field(description="Current extensions")


# ---------------------------------------------------------------- tags

class PublicTag(BaseModel):
    name: str = Field(description="Use it in `tag=` and in tags/{tag}/{path}")
    labels: list[str] = Field(description="Issue labels that put this tag on the folders of an open issue")


class PrivateTag(BaseModel):
    name: str = Field(description="Use it in `my_tag=` and in my-tags/{tag}/{path}")


class TagIssue(BaseModel):
    number: int
    title: str
    author: str | None
    created_at: str


class Tag(BaseModel):
    id: int
    label: str = Field(description="The tag's name")
    color: str = Field(description='A colour name, "" for none')
    scope: Literal["public", "private"] = Field(description="private: the token's account's own tag")
    manual: bool = Field(description="Put on by hand (by_login, at); else only inherited from issues")
    by_login: str | None
    at: str | None
    issues: list[TagIssue] = Field(description="Open issues whose labels put this public tag on the folder")


class FacetValue(BaseModel):
    value: str
    n: int = Field(description="Current extensions with this value")


class TagFacet(BaseModel):
    value: int = Field(description="The tag's id")
    label: str = Field(description="The tag's name, for `tag=` or `my_tag=`")
    color: str
    n: int


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
    op: str = Field(description="Monitor step: browse, latest, search, details, chapters, read, pages")
    label: str = Field(description="The step in words")
    status: str = Field(description="pass, fail, maybe, skip, na, ...")
    status_label: str | None = Field(description="The result in words")
    error: str | None


class ExtensionItem(BaseModel):
    path: str = Field(description="The folder, in the repository's layout")
    lang: str | None
    module: str | None
    name: str | None
    package_name: str | None
    theme: str | None
    content_warning: str | None = Field(description="SAFE, MIXED or NSFW")
    lib_version: str | None
    version_code: int | None
    icon_url: str | None
    removed: bool
    status: str | None = Field(description=STATUS)
    status_date: str | None = Field(description="Day of the latest monitor run")
    status_since: str | None = Field(description="Day the status started")
    status_days: int | None
    status_since_open: bool | None = Field(description="The status dates from the first monitor run, so it may be older")
    ops: list[OpResult] = Field(description="Per step results of the run that decided the status")
    rechecked: bool = Field(description="The status comes from a recheck through proxies")
    downloads_7d: int | None
    downloads_all: int | None
    last_changed: str | None = Field(description="Date of the folder's last commit")
    open_issues: int
    pr_count: int
    open_prs: int
    hosts: list[str] = Field(description="Current base domains")
    tags: list[Tag]


class ExtensionPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ExtensionItem]


# ---------------------------------------------------------------- folders

class FolderDomain(BaseModel):
    host: str
    role: str = Field(description="base, mirror or deeplink")
    from_sha: str
    from_date: str | None
    to_sha: str | None = Field(description="null: still declared")
    to_date: str | None


class FolderPr(BaseModel):
    number: int
    title: str
    state: str = Field(description=STATE)
    author: str | None
    created_at: str
    merged_at: str | None
    closed_at: str | None
    single_target: bool = Field(description="The PR changes this folder only")
    folder_count: int


class FolderIssue(BaseModel):
    number: int
    title: str
    state: str = Field(description=STATE)
    created_at: str
    closed_at: str | None
    author: str | None
    methods: Csv = Field(description="How it was linked: title-name, form-name, domain, closing-pr, manual")
    confidence: float
    manual: bool = Field(description="Linked by hand")


class Commit(BaseModel):
    sha: str
    commit_date: str
    subject: str
    body: str | None
    author: str | None
    pr_number: int | None
    pr_title: str | None
    pr_merged_at: str | None
    pr_created_at: str | None


class Source(BaseModel):
    source_id: str
    stable_id: str | None = Field(description="The id source links use")
    name: str | None
    lang: str | None
    base_url: str | None
    url_type: str | None = Field(description="static, custom or mirrors")
    mirror_urls: list[str]


class RecheckTry(BaseModel):
    from_: str = Field(alias="from", serialization_alias="from", description="Proxy country")
    status: str = Field(description="pass, fail, maybe or error")
    text: str = Field(description="The outcome in words")
    ops: list[OpResult]


class Recheck(BaseModel):
    decided: bool = Field(description="The recheck set the status; else the sweep's verdict stands")
    verdict: str | None = Field(description="In words")
    tries: list[RecheckTry]
    ops: list[OpResult] = Field(description="Steps of the deciding try")
    notes: list[str]


class Status(BaseModel):
    status: str = Field(description=STATUS)
    date: str
    since: str | None
    since_open: bool = Field(description="The status dates from the first monitor run, so it may be older")
    ops: list[OpResult]
    recheck: Recheck | None


class StatusPoint(BaseModel):
    date: str
    status: str


class Downloads(BaseModel):
    h1: int | None
    h24: int | None
    d7: int | None
    d30: int | None
    all_time: int | None
    date: str


class Side(BaseModel):
    origin: str
    stable_id: str
    path: str | None
    name: str | None
    lang: str | None
    hosts: list[str] = Field(description="Current hosts")


class SourceRef(BaseModel):
    origin: str = Field(description=ORIGIN)
    stable_id: str = Field(description="A source's stable_id (folder `sources`)")


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
    host: str | None = Field(description="The host that linked them")
    since: str | None
    manual: bool
    by_login: str | None
    state: str = Field(description=LINK_STATE)
    behind: str | None = Field(description=BEHIND)
    basis: str | None = Field(description=BASIS)
    source_link: Annotated[SourcePair, BeforeValidator(_source_link)] = Field(
        validation_alias=AliasChoices("source_link", "key"), description="The body for /api/v1/source-links")


class LinkStatus(BaseModel):
    status: str = Field(description=STATUS)
    since: str | None


class ElsewhereFolder(BaseModel):
    path: str
    name: str | None
    state: str = Field(description="The worst of its pairs: " + LINK_STATE)
    behind: str | None = Field(description=BEHIND)
    basis: str | None = Field(description=BASIS)
    manual: bool
    by_login: str | None
    hosts: list[str]
    icon_url: str | None
    removed: bool
    open_issues: int
    status: LinkStatus | None
    pairs: list[LinkPair] = Field(description="One per linked source")


class Elsewhere(BaseModel):
    links: dict[str, list[ElsewhereFolder]] = Field(description="Linked folders, by other repository")
    hidden: list[LinkPair] = Field(description="Automatic links an editor hid")


class ThemeFolder(BaseModel):
    path: str
    history: list[Commit]
    issues: list[FolderIssue]
    domains: list[FolderDomain] = Field(description="Current domains only")


class ThemeExtension(BaseModel):
    path: str
    name: str | None
    lang: str | None
    removed: bool
    status: str | None = Field(description=STATUS)


class Folder(BaseModel):
    """An extension (kind src) or theme folder. Fields marked "src" or "theme" are present for that kind only."""
    origin: str
    path: str
    kind: str = Field(description="src (an extension) or theme")
    lang: str | None
    module: str | None
    name: str | None
    package_name: str | None
    theme: str | None
    theme_path: str | None
    content_warning: str | None = Field(description="SAFE, MIXED or NSFW")
    lib_version: str | None
    version_code: int | None
    icon_url: str | None
    source_url: str | None
    first_sha: str | None
    last_sha: str | None
    removed_sha: str | None = Field(description="null: not removed")
    first_date: str | None
    last_date: str | None
    domains: list[FolderDomain]
    prs: list[FolderPr]
    issues: list[FolderIssue]
    history: list[Commit]
    tags: list[Tag] = Field(description="Public and inherited tags, plus your private ones with a token")
    sources: list[Source] | None = Field(None, description="src")
    apk_url: str | None = Field(None, description="src")
    status: Status | None = Field(None, description="src; null: not monitored")
    status_history: list[StatusPoint] | None = Field(None, description="src; the last 60 runs, oldest first")
    downloads: Downloads | None = Field(None, description="src; null: not tracked")
    elsewhere: Elsewhere | None = Field(None, description="src; links to other repositories")
    theme_folder: ThemeFolder | None = Field(None, description="src on a theme: the theme's own history, issues and domains")
    extensions: list[ThemeExtension] | None = Field(None, description="theme: the extensions built on it")


# ---------------------------------------------------------------- issues and PRs

class IssueLink(BaseModel):
    path: str
    kind: str | None
    name: str | None
    lang: str | None
    theme: str | None
    theme_path: str | None
    methods: Csv = Field(description="title-name, form-name, domain, closing-pr, manual")
    confidence: float
    manual: bool
    by_login: str | None = Field(description="Who linked it by hand")


class RemovedLink(BaseModel):
    path: str
    at: str
    by_login: str | None


class IssuePr(BaseModel):
    number: int
    title: str
    state: str = Field(description=STATE)
    merged_at: str | None


class Issue(BaseModel):
    origin: str
    number: int
    title: str
    state: str = Field(description=STATE)
    author: str | None
    created_at: str
    closed_at: str | None
    body: str | None
    labels: list[str]
    links: list[IssueLink] = Field(description="Folders it is about")
    removed_links: list[RemovedLink] = Field(description="Automatic links an editor hid")
    prs: list[IssuePr] = Field(description="PRs that close it")


class FolderName(BaseModel):
    path: str
    kind: str | None
    name: str | None
    lang: str | None
    theme: str | None
    theme_path: str | None


class Ref(BaseModel):
    number: int
    title: str
    state: str = Field(description=STATE)


class PrCommit(BaseModel):
    sha: str
    commit_date: str = Field(validation_alias=AliasChoices("commit_date", "date"))
    subject: str
    body: str | None


class PullRequest(BaseModel):
    origin: str
    number: int
    title: str
    state: str = Field(description=STATE)
    author: str | None
    created_at: str
    merged_at: str | None
    closed_at: str | None
    body: str | None
    single_target: str | None = Field(description="The one folder it changes, if it changes exactly one")
    folders: list[FolderName]
    issues: list[Ref] = Field(description="Issues it closes")
    commits: list[PrCommit] = Field(description="Its commits on the default branch")


# ---------------------------------------------------------------- domains and search

class DomainItem(BaseModel):
    host: str
    current_extensions: int
    all_extensions: int
    roles: Csv = Field(description="base, mirror, deeplink")


class DomainPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DomainItem]


class DomainUsage(FolderDomain):
    path: str
    name: str | None
    lang: str | None
    kind: str | None
    removed: bool


class Domain(BaseModel):
    host: str
    usages: list[DomainUsage] = Field(description="Current first, subdomains included")


class Search(BaseModel):
    query: str
    issue: Ref | None = Field(description="For #123 or 123")
    pr: Ref | None
    domains: list[DomainItem] = Field(description="The first 100")
    domains_total: int
    extensions: list[ExtensionItem] = Field(description="The first 100, removed ones included")
    extensions_total: int


# ---------------------------------------------------------------- cross-repo

class DriftIgnored(BaseModel):
    reason: str = Field(description="right_side, redirect or other")
    label: str = Field(description="The reason in words")
    by_login: str | None
    at: str


class DriftRow(BaseModel):
    a: Side
    b: Side
    behind: str | None = Field(description=BEHIND)
    basis: str | None = Field(description=BASIS)
    host: str | None = Field(description="The host that once linked them")
    since: str | None
    langs: list[str]
    source_links: SourceLinks = Field(validation_alias=AliasChoices("source_links", "keys"),
                                      description="The linked source pairs, each a body for /api/v1/source-links")
    pair: list[str] = Field(description="[origin_a, path_a, origin_b, path_b]: the body's pair for /api/v1/drift/ignored")
    ignored: DriftIgnored | None


class DriftCounts(BaseModel):
    behind: dict[str, int] = Field(description='Rows not ignored, by the repo behind ("unknown" when not known)')
    ignored: int


class Drift(BaseModel):
    total: int = Field(description="Rows not ignored")
    counts: DriftCounts
    items: list[DriftRow]


class Tracked(BaseModel):
    status: bool = Field(description="The monitor covers this repository")
    downloads: bool = Field(description="Download counts exist for it")


class NamedFolder(BaseModel):
    path: str
    name: str | None


class SiteRequest(BaseModel):
    number: int
    title: str
    state: str = Field(description=STATE)
    removed: bool = Field(description='Labelled "Previously deleted": the repo dropped the site on purpose')


class MissingItem(BaseModel):
    path: str
    name: str | None
    lang: str | None
    icon_url: str | None
    content_warning: str | None
    status: str | None = Field(description=STATUS)
    status_since: str | None
    downloads_7d: int | None
    hosts: list[str]
    removed_in_target: list[NamedFolder] = Field(description="Linked folders of the target that were removed")
    same_name: NamedFolder | None = Field(description="An unlinked target folder with the same name: maybe the same site")
    requests: list[SiteRequest] = Field(description="The target's site-request issues naming one of its domains")


class MissingOrigin(BaseModel):
    tracked: Tracked
    items: list[MissingItem]


class Missing(BaseModel):
    target: str
    origins: dict[str, MissingOrigin] = Field(description="Sites the target lacks, by repository that has them")
    total: int


# ---------------------------------------------------------------- writes

class SourceLinkResult(SourcePair):
    override: Literal["add", "remove"] | None = Field(description="The manual decision now in place (null: automatic)")


DRIFT_REASON = Literal["right_side", "redirect", "other"]


class DriftIgnore(BaseModel):
    pair: list[str] = Field(description="The row's `pair` from GET /api/v1/drift: [origin_a, path_a, origin_b, path_b]",
                            min_length=4, max_length=4)
    reason: DRIFT_REASON | None = Field(
        None, description="PUT only: right_side (\"behind\" has the better domain), redirect (the old domain redirects), "
                          "other (not worth fixing)")


class DriftIgnoreResult(BaseModel):
    pair: list[str]
    reason: DRIFT_REASON | None = Field(description="null: back in the list")
