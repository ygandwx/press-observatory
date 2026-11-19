"""Editorial screening shared by collection, manual entry and historical review."""

import re
from urllib.parse import unquote, urlsplit

SUBJECTS = "报社 报业 报团 行业协会 新闻出版主管部门 新闻出版局 传媒集团 融媒体中心 通讯社 广电 电视台 期刊社 杂志社 编辑部 新闻宣传主管部门 国家新闻出版署 行业媒体 客户端 新媒体矩阵 报网端微号 日报 晚报 晨报 媒体 期刊".split()
ACTIONS = "改革 融合 转型 整合 合并 重组 撤并 停刊 休刊 改版 创刊 更名 上线 推出 发布 客户端 矩阵 智库 大模型 AI 智能体 数智化 平台建设 战略签约 签署 人事任免 任命 履新 出任 荣获 获评 获得 委任 免去 担任 社长调整 总编辑调整 获奖 入选 重大项目落地 经营模式创新".split()
GEO_TERMS = (
    "GEO",
    "生成式引擎优化",
    "生成式搜索优化",
    "大模型优化",
    "AI可见度",
    "可信传播",
    "智能体检索优化",
)
NOISE_TERMS = {
    "招标采购": "招标 采购 中标 成交公告 询价 竞争性谈判 竞争性磋商 比选 招标代理 投标 分包 供应商 服务商遴选 资质审查 挂网 招采 服务外包 物资采购 设备采购 服务采购 项目发包 承包商 供应商入库 框架协议采购 废标 流标 采购意向 遴选公告 成交结果 磋商公告 询比".split(),
    "招聘": "招聘 应聘 岗位空缺 招考 报名条件 薪资 简历投递 人事代理 拟聘 公招 拟录用 公开招录".split(),
    "广告": "广告招商 版面广告 刊例价 订报 订阅优惠 征订 软文推广 展位 赞助招募 广告刊例".split(),
    "第三方新闻": "医院 学校 高校 企业IPO 楼盘 旅游线路 消费品促销 明星娱乐 交通事故 天气 天气预警".split(),
}
POSITIVE_SUBJECTS = ("媒体自身", "传媒行业", "政府主管部门")
SUBJECT_TYPES = (*POSITIVE_SUBJECTS, "第三方企业机构", "其他")
POSITIVE_NATURES = (
    "改革融合",
    "平台矩阵",
    "技术 AI",
    "经营模式",
    "人事任免",
    "政策规范",
    "停刊改版",
    "获奖入选",
    "战略合作",
)
NEGATIVE_NATURES = ("招标采购", "招聘", "广告招商", "第三方新闻")
NATURES = (*POSITIVE_NATURES, *NEGATIVE_NATURES, "其他")
BLOCKED_PATH = re.compile(
    r"(?:^|[/_.-])(procurement|bidding|tender|recruit(?:ment)?|zhaopin|zhaobiao|zhaocai|zbcg|caigou|advertising|dingyue|careers?)(?:[/_.-]|$)",
    re.I,
)
BLACK_ZONES = (
    "招标采购",
    "招采公告",
    "采购信息",
    "政府采购",
    "服务大厅",
    "招标代理",
    "招聘启事",
    "招聘公告",
    "公开招聘",
    "招标公告",
    "招租公告",
    "预算与参评",
    "广告招商",
    "订报订阅",
    "广告刊例",
    "活动会展报名",
    "投诉建议",
    "读者来信",
)
FORMAL_NOTICE = re.compile(
    r"(中标|招标|废标|流标|成交(结果|公告)|竞争性(谈判|磋商)|询价|比选|采购.{0,25}(公告|项目|结果|公示|意向|通知)|项目.{0,18}采购|采购人(信息|名称)|供应商(资格|征集|入库)|采购编号)"
)
DEPLOYMENT = re.compile(
    r"(正式上线|已上线|投入运行|建成投用|推出|启用|上线|完成部署|上线运行|任命|出任|合并|重组|停刊|改版)"
)
INCIDENTAL = re.compile(
    r"(提[及到]|此前|曾经?|前期|前期的|涵盖|包括|涉及|谈及).{0,12}(采购|招标)|采购.{0,8}(后|之外)"
)
REPORTER = re.compile(r"(报道[：:]|记者(报道|获悉)|据.{1,15}报道[，,：:]|消息[：:])")
SCOPE = ("媒体官网", "行业垂类", "监管平台", "行业协会")


def compact(text):
    return re.sub(r"\s+", "", str(text or "")).casefold()


def word_present(text, word):
    if word.isascii() and word.isalnum():
        return bool(
            re.search(
                r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])",
                str(text or ""),
                re.I,
            )
        )
    return bool(word.strip()) and compact(word) in compact(text)


def hits(text, terms):
    return [word for word in terms if word_present(text, word)]


def geo_terms(text):
    return hits(text, GEO_TERMS)


def is_geo(text):
    return bool(geo_terms(text))


MEDIA_SECTIONS = {
    "社内动态": (
        "社内动态",
        "报社动态",
        "杂志社动态",
        "社务动态",
        "机构动态",
        "报网动态",
        "本社活动",
        "求是动态",
        "工作动态",
    ),
    "集团新闻": ("集团新闻", "集团动态", "集团要闻", "公司动态"),
    "融媒动态": ("融媒动态", "融媒体动态"),
    "发展综述": ("发展综述", "社会责任报告", "媒体责任报告"),
    "关于我们": ("关于我们",),
    "机构公告": (
        "机构公告",
        "报社公告",
        "公示公告",
        "社务公示",
        "公告公示",
        "工事公告",
        "运营公告",
        "通知公告",
        "报社公示",
        "本站公告",
        "公告栏",
    ),
    "媒体矩阵": ("媒体矩阵",),
    "产品与平台": ("产品与平台", "产品平台"),
}
AUTHORITY_SECTIONS = ("行业专题", "监管发布", "协会动态")


def source_block(source, registered=False):
    location = blocked_location(source.url, source.name)
    if location:
        return location
    if source.adapter == "人工":
        return "该渠道需授权数据源或人工巡检"
    if source.zone not in SCOPE:
        return "来源类型未经核验"
    section = source.profile.get("section", "")
    path = urlsplit(source.url).path.strip("/")
    if (
        not path
        and not source.profile.get("selector")
        and not source.profile.get("documents")
    ):
        return "仅提供首页，尚无核验的栏目入口"
    publisher, sep, label = source.name.partition("·")
    named_section = any(hits(label, terms) for terms in MEDIA_SECTIONS.values())
    owner_matches = (
        registered
        and sep
        and publisher == source.profile.get("publisher")
        and bool(
            source.profile.get("selector")
            or source.profile.get("embedded_list_selector")
        )
    )
    # The older registry labelled group-owned publications as industry sources.
    # Only a matching per-URL registration, publisher and reviewed listing can
    # recover such a group section; a hostname or role by itself is insufficient.
    host = (urlsplit(source.url).hostname or "").lower()
    if (
        owner_matches
        and host.endswith(".gov.cn")
        and source.profile.get("subject_terms")
        and re.search(r"(报社|传媒集团|报业总社)(动态|公告)$", label)
    ):
        return ""
    if owner_matches and (
        (label == "公告" or re.fullmatch(r"[一-龥]{1,8}网公告", label)) or named_section
    ):
        return ""
    if section:
        accepted = (
            tuple(MEDIA_SECTIONS)
            if source.zone == "媒体官网"
            else (dict(zip(SCOPE[1:], AUTHORITY_SECTIONS))[source.zone],)
        )
        return "" if section in accepted else "栏目与核验的信源类型不符"
    if source.zone == "媒体官网":
        if section in MEDIA_SECTIONS or (
            named_section
            if sep
            else any(hits(source.name, terms) for terms in MEDIA_SECTIONS.values())
        ):
            return ""
        return "栏目未列入媒体官网白名单"
    # A third-party source needs a specific reviewed section. The editor records
    # its role when registering it. Annual directories additionally pin each URL.
    if (
        source.profile.get("documents")
        or section == dict(zip(SCOPE[1:], AUTHORITY_SECTIONS))[source.zone]
    ):
        return ""
    return "权威行业、监管或协会栏目尚未核验"


def blocked_location(url, zone=""):
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if host == "ccgp.gov.cn" or host.endswith(".ccgp.gov.cn"):
        return "政府采购"
    if match := BLOCKED_PATH.search(path):
        return match.group(1)
    return next(iter(hits(zone + " " + path, BLACK_ZONES)), "")


def location_reason(term):
    token = term.casefold()
    if token in (
        "政府采购",
        "招采公告",
        "采购信息",
        "招标采购",
        "招标公告",
        "招标代理",
        "procurement",
        "bidding",
        "tender",
        "zhaobiao",
        "zhaocai",
        "zbcg",
        "caigou",
    ):
        return "招标采购"
    if token.startswith(("招聘", "公开招聘", "recruit", "career", "zhaopin")):
        return "招聘"
    if token in ("广告招商", "广告刊例", "订报订阅", "advertising", "dingyue"):
        return "广告"
    return "其他"


def assess(title, body="", url="", subjects=(), actions=(), exclusions=(), muted=None):
    """Return evidence. A hard negative is distinct from insufficient positive evidence."""
    location = blocked_location(url)
    if location:
        return {
            "reason": location_reason(location),
            "terms": [location],
            "gate": "信源黑名单",
        }
    muted = muted or {}

    def enabled(terms, kind):
        return [word for word in terms if word not in muted.get(kind, ())]

    subject_hits = hits(title, enabled([*SUBJECTS, *subjects], "主体词"))
    action_hits = hits(title, enabled([*ACTIONS, *GEO_TERMS, *actions], "动作词"))
    positive = bool(
        subject_hits and action_hits and set(subject_hits + action_hits) != {"客户端"}
    )
    if re.search(
        r"推荐.{0,12}(?:参评|评选|候选)|参评.{0,32}(?:公示|初评)|拟(?:获奖|授予).{0,25}公示",
        title,
    ) and not re.search(r"已获奖|荣获", title):
        return {"reason": "", "terms": ["参评阶段"], "gate": "参评非获奖结果"}
    enabled_noise = {
        group: enabled(words, "排除词") for group, words in NOISE_TERMS.items()
    }
    title_hits = {group: hits(title, terms) for group, terms in enabled_noise.items()}
    evidence = body[:7500]
    if FORMAL_NOTICE.search(title) and not (
        INCIDENTAL.search(title)
        and DEPLOYMENT.search(title)
        and not re.search(r"(公告|公示|招标文件|采购意向|结果通知)$", title)
    ):
        return {
            "reason": "招标采购",
            "terms": title_hits["招标采购"] or [FORMAL_NOTICE.search(title)[0]],
            "gate": "排除词",
        }
    for group, words in title_hits.items():
        body_hits = hits(evidence, enabled_noise[group])
        if not words and not body_hits:
            continue
        if group == "第三方新闻":
            # A media partner is not necessarily the subject. The headline's acting
            # institution and any reporting attribution are stronger evidence.
            if not words or (
                subject_hits
                and not REPORTER.search(title)
                and not re.search(
                    r"(医院|学校|高校).{0,12}(上线|推出|引入|发布|启动|部署|启用)",
                    title,
                )
            ):
                continue
        elif group == "招标采购" and positive and DEPLOYMENT.search(title):
            if not words or INCIDENTAL.search(title):
                if not re.search(
                    r"(?:采购人(?:信息|名称)|投标截止时间|投标人资格).{0,45}[:：]",
                    evidence,
                ):
                    continue
        elif (
            not words
            and positive
            and DEPLOYMENT.search(title)
            and group in ("招聘", "广告")
        ):
            if not re.search(r"简历投递|报名条件|刊例价|广告招商电话", evidence):
                continue
        return {"reason": group, "terms": words or body_hits, "gate": "排除词"}
    # Govern ordinary keyword matches without reclassifying a known incidental
    # mention as a hard exclusion a second time.
    screened = {compact(w) for terms in NOISE_TERMS.values() for w in terms}
    custom = hits(
        title + " " + evidence, [w for w in exclusions if compact(w) not in screened]
    )
    if custom:
        return {"reason": "其他", "terms": custom, "gate": "排除词"}
    return {
        "reason": "",
        "terms": list(dict.fromkeys(subject_hits + action_hits)),
        "gate": "" if positive else "双命中不足",
    }


def historical_assessment(event):
    evidence = list(event.articles.all())
    for article in evidence:
        result = assess(article.title, article.excerpt, article.url)
        if result["reason"]:
            return result
    return assess(event.title, event.summary)
