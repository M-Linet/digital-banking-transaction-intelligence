"""Generate the complete Power BI project (PBIP: TMDL semantic model + PBIR report) for Tausi Digital Bank.

    python scripts/build_pbip.py --data-folder "C:\\path\\to\\data\\processed\\powerbi\\"

Writes powerbi/TausiDigitalBank.pbip, powerbi/TausiDigitalBank.SemanticModel/ and powerbi/TausiDigitalBank.Report/.
Open the .pbip in Power BI Desktop and click Refresh.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PB = ROOT / "powerbi"
NAME = "TausiDigitalBank"
SM = PB / f"{NAME}.SemanticModel"
RP = PB / f"{NAME}.Report"
NS = uuid.UUID("7d4e1f63-2a9b-4c8e-b5d1-3e6f8a0c9b21")
tag = lambda s: str(uuid.uuid5(NS, s))
hid = lambda *p: hashlib.sha1("|".join(map(str, p)).encode()).hexdigest()[:20]

# ------------------------------------------------------------------------------------------------
# Semantic model
# ------------------------------------------------------------------------------------------------
T, I, D, C, DT, B = "string", "int64", "double", "decimal", "dateTime", "boolean"
MT = {T: "type text", I: "Int64.Type", D: "type number", C: "Currency.Type", DT: "type date", B: "type logical"}
TABLES = {
    "dim_date": [("date", DT), ("year", I), ("month_number", I), ("month_short", T), ("year_month", T), ("month_start", DT),
                 ("iso_dow", I), ("weekday", T), ("is_payday_window", B)],
    "dim_channel": [("channel", T), ("channel_group", T)],
    "dim_segment": [("segment", T), ("segment_order", I)],
    "dim_customer": [("customer_id", T), ("segment", T), ("age_group", T), ("county", T), ("onboarding_channel", T), ("kyc_tier", T),
                     ("onboarding_month", T), ("activity_status", T), ("successful_txns", I), ("value_kes", C), ("fee_income", C)],
    "fact_txn_daily": [("date", DT), ("channel", T), ("transaction_type", T), ("type_category", T), ("segment", T), ("attempts", I),
                       ("successful", I), ("operational_failures", I), ("customer_failures", I), ("value_kes", C), ("fee_income", C),
                       ("overcharge_kes", C)],
    "fact_failures": [("date", DT), ("channel", T), ("failure_reason", T), ("failure_owner", T), ("failures", I), ("retried", I),
                      ("recovered", I), ("not_recovered", I), ("fees_lost_kes", C)],
    "fact_channel_hour": [("date", DT), ("hour_of_day", I), ("channel", T), ("attempts", I), ("operational_failures", I)],
    "fact_customer_month": [("customer_id", T), ("segment", T), ("month", DT), ("is_active", B), ("is_new_customer", B), ("successful", I),
                            ("value_kes", C), ("fee_income", C), ("operational_failures", I)],
    "market_context": [("month", DT), ("bank_volume_index", D), ("market_volume_index", D), ("bank_value_index", D), ("market_value_index", D)],
}
HIDDEN = {"fact_txn_daily": {"date", "channel", "segment"}, "fact_failures": {"date", "channel"},
          "fact_channel_hour": {"date", "channel"}, "fact_customer_month": {"customer_id", "segment", "month"},
          "dim_segment": {"segment_order"}}
SORT_BY = {("dim_date", "month_short"): "month_number", ("dim_date", "weekday"): "iso_dow", ("dim_segment", "segment"): "segment_order"}
SUM_COLS = {"attempts", "successful", "operational_failures", "customer_failures", "value_kes", "fee_income", "overcharge_kes",
            "failures", "retried", "recovered", "not_recovered", "fees_lost_kes"}
RELS = [("fact_txn_daily.date", "dim_date.date"), ("fact_txn_daily.channel", "dim_channel.channel"), ("fact_txn_daily.segment", "dim_segment.segment"),
        ("fact_failures.date", "dim_date.date"), ("fact_failures.channel", "dim_channel.channel"),
        ("fact_channel_hour.date", "dim_date.date"), ("fact_channel_hour.channel", "dim_channel.channel"),
        ("fact_customer_month.month", "dim_date.date"), ("fact_customer_month.segment", "dim_segment.segment"),
        ("dim_customer.segment", "dim_segment.segment")]

KES, PCT2, NUM, DEC1 = "#,0", "0.00%", "#,0", "#,0.0"
FTD, FF, FCM, FCH = "fact_txn_daily", "fact_failures", "fact_customer_month", "fact_channel_hour"
MEASURES = [
    ("Attempts", f"SUM ( {FTD}[attempts] )", NUM, "1 Volume & value"),
    ("Successful Transactions", f"SUM ( {FTD}[successful] )", NUM, "1 Volume & value"),
    ("Value Moved (KES)", f"SUM ( {FTD}[value_kes] )", KES, "1 Volume & value"),
    ("Share of Transactions", "DIVIDE ( [Successful Transactions], CALCULATE ( [Successful Transactions], REMOVEFILTERS ( dim_channel ) ) )", "0.0%", "1 Volume & value"),
    ("Share of Value", f"DIVIDE ( [Value Moved (KES)], CALCULATE ( [Value Moved (KES)], REMOVEFILTERS ( {FTD}[transaction_type], {FTD}[type_category] ) ) )", "0.0%", "1 Volume & value"),
    ("Fee Income (KES)", f"SUM ( {FTD}[fee_income] )", KES, "2 Fees"),
    ("Fee per Successful Transaction", "DIVIDE ( [Fee Income (KES)], [Successful Transactions] )", "#,0.00", "2 Fees"),
    ("Fee Yield (bps)", "DIVIDE ( [Fee Income (KES)], [Value Moved (KES)] ) * 10000", DEC1, "2 Fees"),
    ("Share of Fees", f"DIVIDE ( [Fee Income (KES)], CALCULATE ( [Fee Income (KES)], REMOVEFILTERS ( {FTD}[transaction_type], {FTD}[type_category] ) ) )", "0.0%", "2 Fees"),
    ("Overcharge to Refund (KES)", f"SUM ( {FTD}[overcharge_kes] )", KES, "2 Fees"),
    ("Fees Lost to Failures (KES)", f"SUM ( {FF}[fees_lost_kes] )", KES, "2 Fees"),
    ("Success Rate %", "DIVIDE ( [Successful Transactions], [Attempts] )", PCT2, "3 Reliability"),
    ("Operational Failure Rate %", f"DIVIDE ( SUM ( {FTD}[operational_failures] ), [Attempts] )", PCT2, "3 Reliability"),
    ("Customer Failure Rate %", f"DIVIDE ( SUM ( {FTD}[customer_failures] ), [Attempts] )", PCT2, "3 Reliability"),
    ("Failures", f"SUM ( {FF}[failures] )", NUM, "3 Reliability"),
    ("Operational Failures", f"CALCULATE ( [Failures], {FF}[failure_owner] = \"Operational\" )", NUM, "3 Reliability"),
    ("Recovered by Retry %", f"DIVIDE ( CALCULATE ( SUM ( {FF}[recovered] ), {FF}[failure_owner] = \"Operational\" ), [Operational Failures] )", "0.0%", "3 Reliability"),
    ("Unrecovered Operational Failures", f"CALCULATE ( SUM ( {FF}[not_recovered] ), {FF}[failure_owner] = \"Operational\" )", NUM, "3 Reliability"),
    ("Hourly Operational Failure Rate %", f"DIVIDE ( SUM ( {FCH}[operational_failures] ), SUM ( {FCH}[attempts] ) )", PCT2, "3 Reliability"),
    ("Customer-Months", f"COUNTROWS ( {FCM} )", NUM, "4 Customers"),
    ("Active Customers", f"CALCULATE ( COUNTROWS ( {FCM} ), {FCM}[is_active] = TRUE () )", NUM, "4 Customers"),
    ("Active Rate %", "DIVIDE ( [Active Customers], [Customer-Months] )", "0.0%", "4 Customers"),
    ("Latest Month", f"MAX ( {FCM}[month] )", "mmm yyyy", "4 Customers"),
    ("Active Customers (latest month)", f"VAR m = [Latest Month] RETURN CALCULATE ( [Active Customers], {FCM}[month] = m )", NUM, "4 Customers"),
    ("Active Rate % (latest month)", f"VAR m = [Latest Month] RETURN CALCULATE ( [Active Rate %], {FCM}[month] = m )", "0.0%", "4 Customers"),
    ("New Customers", f"CALCULATE ( COUNTROWS ( {FCM} ), {FCM}[is_new_customer] = TRUE () )", NUM, "4 Customers"),
    ("Customers", "COUNTROWS ( dim_customer )", NUM, "4 Customers"),
    ("Dormant Customers", "CALCULATE ( [Customers], dim_customer[activity_status] = \"Dormant (90+ days)\" )", NUM, "4 Customers"),
    ("Dormant %", "DIVIDE ( [Dormant Customers], CALCULATE ( [Customers], dim_customer[activity_status] <> \"Too new to assess\" ) )", "0.0%", "4 Customers"),
    ("Fee per Active Customer-Month", f"DIVIDE ( SUM ( {FCM}[fee_income] ), [Active Customers] )", "#,0.0", "4 Customers"),
    ("Bank Volume Index", "AVERAGE ( market_context[bank_volume_index] )", DEC1, "5 Market context"),
    ("Market Volume Index (CBK)", "AVERAGE ( market_context[market_volume_index] )", DEC1, "5 Market context"),
]

# ---- styling measures: colours follow the validated palette (blue = focus, orange = problem, grey = context) ----
BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#c3c2b7"


def _growth(m, pct=True):
    """KPI delta text: first vs last month in the current selection. Arrows carry the direction, never colour alone."""
    fmt = '"▲ 0%;▼ 0%;0%"' if pct else '"▲ 0.0 pts;▼ 0.0 pts;0.0 pts"'
    diff = "DIVIDE ( b - a, a )" if pct else "( b - a ) * 100"
    return ("VAR f = MIN ( dim_date[month_start] )\nVAR l = MAX ( dim_date[month_start] )\n"
            f"VAR a = CALCULATE ( [{m}], dim_date[month_start] = f )\nVAR b = CALCULATE ( [{m}], dim_date[month_start] = l )\n"
            f"RETURN IF ( NOT ISBLANK ( a ) && a <> 0, FORMAT ( {diff}, {fmt} ) & \"  \" & FORMAT ( f, \"mmm yy\" ) & \" → \" & FORMAT ( l, \"mmm yy\" ) )")


MEASURES += [
    ("Δ Successful Transactions", _growth("Successful Transactions"), None, "6 Formatting"),
    ("Δ Value Moved", _growth("Value Moved (KES)"), None, "6 Formatting"),
    ("Δ Fee Income", _growth("Fee Income (KES)"), None, "6 Formatting"),
    ("Δ Success Rate", _growth("Success Rate %", pct=False), None, "6 Formatting"),
    ("Δ Active Customers", _growth("Active Customers"), None, "6 Formatting"),
    ("Δ Active Rate", _growth("Active Rate %", pct=False), None, "6 Formatting"),
    ("Δ Fee per Transaction", _growth("Fee per Successful Transaction"), None, "6 Formatting"),
    ("Δ Operational Failure Rate", _growth("Operational Failure Rate %", pct=False), None, "6 Formatting"),
    ("Fees Lost Share Text", 'FORMAT ( DIVIDE ( [Fees Lost to Failures (KES)], [Fee Income (KES)] ), "0.0%" ) & " of fee income"', None, "6 Formatting"),
    ("Dormant Share Text", 'FORMAT ( [Dormant %], "0%" ) & " of those joined before Aug 26"', None, "6 Formatting"),
    ("Colour Fee Channel", f'IF ( SELECTEDVALUE ( dim_channel[channel] ) = "Mobile App", "{BLUE}", "{GREY}" )', None, "6 Formatting"),
    ("Colour Value Channel", f'IF ( SELECTEDVALUE ( dim_channel[channel] ) = "Internet Banking", "{BLUE}", "{GREY}" )', None, "6 Formatting"),
    ("Colour Failure Channel", f'IF ( SELECTEDVALUE ( dim_channel[channel] ) IN {{ "USSD", "Agent" }}, "{ORANGE}", "{GREY}" )', None, "6 Formatting"),
    ("Colour Failure Owner", f'IF ( SELECTEDVALUE ( fact_failures[failure_owner] ) = "Operational", "{ORANGE}", "{GREY}" )', None, "6 Formatting"),
    ("Channel Status", 'VAR r = [Operational Failure Rate %] RETURN IF ( ISBLANK ( r ), BLANK (), IF ( r >= 0.025, "▲ Watch", "● Normal" ) )', None, "6 Formatting"),
    ("Colour Channel Status", f'IF ( [Operational Failure Rate %] >= 0.025, "{ORANGE}", "#52514e" )', None, "6 Formatting"),
    ("Colour Yield Channel", f'IF ( SELECTEDVALUE ( dim_channel[channel] ) IN {{ "Agent", "ATM" }}, "{BLUE}", "{GREY}" )', None, "6 Formatting"),
    ("Colour Segment Focus", f'IF ( SELECTEDVALUE ( dim_segment[segment] ) IN {{ "Youth", "Mass Retail" }}, "{ORANGE}", "{GREY}" )', None, "6 Formatting"),
]


def q(n: str) -> str:
    return n if n.replace("_", "").isalnum() else "'" + n.replace("'", "''") + "'"


def m_source(table, cols):
    types = ", ".join(f'{{"{c}", {MT[t]}}}' for c, t in cols)
    return ["let",
            f'    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
            "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")', "in", "    Typed"]


def table_tmdl(name, cols):
    out = [f"table {name}", f"\tlineageTag: {tag(name)}"] + (["\tdataCategory: Time"] if name == "dim_date" else []) + [""]
    for c, t in cols:
        out += [f"\tcolumn {q(c)}", f"\t\tdataType: {t}"]
        if name == "dim_date" and c == "date":
            out.append("\t\tisKey")
        out.append({DT: "\t\tformatString: dd mmm yyyy", C: "\t\tformatString: #,0", I: "\t\tformatString: 0"}.get(t, "") or None)
        if c in HIDDEN.get(name, set()):
            out.append("\t\tisHidden")
        summ = "sum" if (c in SUM_COLS and t in (I, D, C)) else "none"
        out += [f"\t\tlineageTag: {tag(name + '.' + c)}", f"\t\tsummarizeBy: {summ}", f"\t\tsourceColumn: {c}"]
        if (name, c) in SORT_BY:
            out.append(f"\t\tsortByColumn: {SORT_BY[(name, c)]}")
        out.append("")
    out += [f"\tpartition {name} = m", "\t\tmode: import", "\t\tsource ="] + ["\t\t\t\t" + l for l in m_source(name, cols)] + [""]
    return "\n".join(l for l in out if l is not None) + "\n"


def measures_tmdl():
    out = ["table _Measures", f"\tlineageTag: {tag('_Measures')}", ""]
    for name, expr, fmt, folder in MEASURES:
        lines = expr.splitlines()
        if len(lines) == 1:
            out.append(f"\tmeasure {q(name)} = {expr}")
        else:
            out.append(f"\tmeasure {q(name)} =")
            out += ["\t\t\t" + l for l in lines]
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        out += [f"\t\tdisplayFolder: {folder}", f"\t\tlineageTag: {tag('m.' + name)}", ""]
    out += ["\tcolumn Dummy", "\t\tdataType: string", "\t\tisHidden", f"\t\tlineageTag: {tag('_Measures.Dummy')}",
            "\t\tsummarizeBy: none", "\t\tsourceColumn: Dummy", "", "\tpartition _Measures = m", "\t\tmode: import", "\t\tsource =",
            "\t\t\t\tlet", "\t\t\t\t    Source = #table(type table [Dummy = text], {})", "\t\t\t\tin", "\t\t\t\t    Source", ""]
    return "\n".join(out) + "\n"


def write_model(data_folder: str):
    d = SM / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    (d / "cultures").mkdir(exist_ok=True)
    for n, cols in TABLES.items():
        (d / "tables" / f"{n}.tmdl").write_text(table_tmdl(n, cols), encoding="utf-8")
    (d / "tables" / "_Measures.tmdl").write_text(measures_tmdl(), encoding="utf-8")
    (d / "relationships.tmdl").write_text("\n".join(f"relationship {tag(a + b)}\n\tfromColumn: {a}\n\ttoColumn: {b}\n" for a, b in RELS), encoding="utf-8")
    (d / "expressions.tmdl").write_text(
        f'expression DataFolder = "{data_folder}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n'
        f"\tlineageTag: {tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n", encoding="utf-8")
    refs = "\n".join(f"ref table {n}" for n in ["_Measures", *TABLES])
    (d / "model.tmdl").write_text("model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\n"
                                  "annotation __PBI_TimeIntelligenceEnabled = 0\n\nannotation PBI_ProTooling = [\"DevMode\"]\n\n"
                                  f"{refs}\n\nref cultureInfo en-US\n", encoding="utf-8")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1550\n\n", encoding="utf-8")
    (d / "cultures" / "en-US.tmdl").write_text('cultureInfo en-US\n\n\tlinguisticMetadata =\n\t\t\t{\n\t\t\t  "Version": "1.0.0",\n\t\t\t  "Language": "en-US"\n\t\t\t}\n\t\tcontentType: json\n\n', encoding="utf-8")
    (SM / "definition.pbism").write_text(json.dumps({"version": "4.0", "settings": {}}, indent=2), encoding="utf-8")
    (SM / ".platform").write_text(json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                                              "metadata": {"type": "SemanticModel", "displayName": NAME},
                                              "config": {"version": "2.0", "logicalId": tag("sm-logical")}}, indent=2), encoding="utf-8")


# ------------------------------------------------------------------------------------------------
# Report (PBIR)
# ------------------------------------------------------------------------------------------------
VC = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.4.0/schema.json"
lit = lambda v: {"expr": {"Literal": {"Value": v}}}
Ms = lambda n: ("Measure", "_Measures", n)
Co = lambda t, c: ("Column", t, c)
fld = lambda r: {r[0]: {"Expression": {"SourceRef": {"Entity": r[1]}}, "Property": r[2]}}


LABELS = {"month_start": "Month", "date": "Date", "channel": "Channel", "segment": "Segment", "transaction_type": "Transaction type",
          "failure_reason": "Failure reason", "hour_of_day": "Hour of day", "activity_status": "Activity status", "month": "Month",
          "Successful Transactions": "Successful", "Value Moved (KES)": "Value moved (KES)", "Fee Income (KES)": "Fee income (KES)",
          "Success Rate %": "Success rate", "Fee Yield (bps)": "Fee yield (bps)", "Share of Value": "Share of value moved",
          "Share of Fees": "Share of fee income", "Bank Volume Index": "Tausi (synthetic)",
          "Market Volume Index (CBK)": "Kenya market, CBK (real)", "Customers": "Customers",
          "Fees Lost to Failures (KES)": "Fees lost (KES)", "Operational Failure Rate %": "Operational failure rate",
          "Hourly Operational Failure Rate %": "Operational failure rate", "Active Rate %": "Active rate", "Dormant %": "Dormant share"}

# ---- brand (Tausi = peacock: deep navy with a gold accent) and validated data colours ----
NAVY, GOLD, INK, INK2, PAGE_BG, PANEL, HAIRLINE = "#13294b", "#eda100", "#0b0b0b", "#52514e", "#eef1f6", "#ffffff", "#dfe3ea"
col = lambda c: {"solid": {"color": lit(f"'{c}'")}}
mcol = lambda m: {"solid": {"color": {"expr": fld(Ms(m))}}}


def proj(r, active=False):
    p = {"field": fld(r), "queryRef": f"{r[1]}.{r[2]}", "nativeQueryRef": r[2]}
    if r[2] in LABELS:
        p["displayName"] = LABELS[r[2]]
    if active:
        p["active"] = True
    return p


def panel(title=None, subtitle=None, subtitle_measure=None, title_size=13, title_colour=NAVY, bg=PANEL, border=True, shadow=True):
    """Container formatting shared by every panel: white rounded card on the tinted page."""
    o = {"background": [{"properties": {"show": lit("true"), "color": col(bg), "transparency": lit("0D")}}],
         "border": [{"properties": {"show": lit("true" if border else "false"), "color": col(HAIRLINE), "radius": lit("12D")}}],
         "dropShadow": [{"properties": {"show": lit("true" if shadow else "false"), "preset": lit("'BottomRight'"),
                                        "color": col(NAVY), "transparency": lit("88D")}}],
         "padding": [{"properties": {"top": lit("8D"), "bottom": lit("6D"), "left": lit("12D"), "right": lit("10D")}}]}
    if title:
        o["title"] = [{"properties": {"show": lit("true"), "text": lit(f"'{title}'"), "fontColor": col(title_colour),
                                      "fontSize": lit(f"{title_size}D"), "bold": lit("true"), "titleWrap": lit("true")}}]
    else:
        o["title"] = [{"properties": {"show": lit("false")}}]
    if subtitle or subtitle_measure:
        text = {"expr": fld(Ms(subtitle_measure))} if subtitle_measure else lit(f"'{subtitle}'")
        o["subTitle"] = [{"properties": {"show": lit("true"), "text": text, "fontColor": col(INK2), "fontSize": lit("10D")}}]
    return o


def visual(page, key, vtype, pos, roles=None, title=None, sort=None, objects=None, filters=None, subtitle=None, container=None):
    x, y, w, h = pos
    v = {"visualType": vtype}
    if roles:
        v["query"] = {"queryState": {r: {"projections": [proj(f, active=(r in ("Category", "Rows") and i == 0 and vtype != "scatterChart"))
                                                         for i, f in enumerate(fs)]} for r, fs in roles.items()}}
        if sort:
            v["query"]["sortDefinition"] = {"sort": [{"field": fld(sort[0]), "direction": sort[1]}], "isDefaultSort": False}
    if vtype in ("lineChart", "clusteredBarChart", "clusteredColumnChart"):
        cat = {"showAxisTitle": lit("false"), "labelColor": col(INK2)}
        if vtype == "clusteredBarChart":
            cat["maxMarginFactor"] = lit("40L")
        val = {"showAxisTitle": lit("false"), "labelColor": col(INK2), "gridlineColor": col("#e8eaee")}
        if vtype == "clusteredBarChart":
            val["show"] = lit("false")                       # bars carry data labels instead of an axis
        objects = {"legend": [{"properties": {"position": lit("'Top'"), "labelColor": col(INK2)}}], **(objects or {}),
                   "categoryAxis": [{"properties": cat}], "valueAxis": [{"properties": val}]}
        if vtype == "clusteredBarChart":
            objects["labels"] = [{"properties": {"show": lit("true"), "color": col(INK), "fontSize": lit("9D")}}]
        if vtype == "lineChart":
            objects["lineStyles"] = [{"properties": {"strokeWidth": lit("3L")}}]
    if objects:
        v["objects"] = objects
    if vtype not in ("textbox",):
        v["visualContainerObjects"] = container or panel(title, subtitle)
    v["drillFilterOtherVisuals"] = True
    out = {"$schema": VC, "name": hid(page, key), "position": {"x": x, "y": y, "z": 0, "height": h, "width": w, "tabOrder": 0}, "visual": v}
    if filters:
        out["filterConfig"] = {"filters": filters}
    return out


def measure_fill(measure):
    """Bar colour driven by a measure (field-value conditional formatting)."""
    return {"dataPoint": [{"properties": {"fill": mcol(measure)}, "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]}


def series_fill(entity, column, mapping, default=None, values=None):
    """Colour each series of a legend by its value: focus series in blue/orange, the rest grey."""
    rules = []
    for v_ in (values or mapping):
        c = mapping.get(v_, default)
        rules.append({"properties": {"fill": col(c)},
                      "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0,
                                   "Left": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": column}},
                                   "Right": {"Literal": {"Value": f"'{v_}'"}}}}}]}})
    return {"dataPoint": rules}


def measure_series_fill(mapping):
    return {"dataPoint": [{"properties": {"fill": col(c)}, "selector": {"metadata": f"_Measures.{m}"}} for m, c in mapping.items()]}


def textbox(page, key, runs, pos, bg=None):
    """runs: list of paragraphs, each a list of (text, size, colour, bold)."""
    paras = [{"textRuns": [{"value": t, "textStyle": {"fontSize": f"{sz}pt", "color": c, **({"fontWeight": "bold"} if b else {})}}
                           for t, sz, c, b in para]} for para in runs]
    v = visual(page, key, "textbox", pos, objects={"general": [{"properties": {"paragraphs": paras}}]})
    if bg:
        v["visual"]["visualContainerObjects"] = {"background": [{"properties": {"show": lit("true"), "color": col(bg), "transparency": lit("0D")}}],
                                                 "border": [{"properties": {"show": lit("false")}}],
                                                 "padding": [{"properties": {"top": lit("6D"), "left": lit("20D")}}]}
    return v


CARD_LABELS = {"Successful Transactions": "Successful transactions", "Value Moved (KES)": "Value moved (KES bn)", "Fee Income (KES)": "Fee income (KES)",
               "Success Rate %": "Success rate", "Active Customers (latest month)": "Active customers, Oct 2026",
               "Active Rate % (latest month)": "Active rate, Oct 2026", "Fee per Successful Transaction": "Fee per transaction (KES)",
               "Fee Yield (bps)": "Fee yield (bps of value)", "Fees Lost to Failures (KES)": "Fees lost to failures (KES)",
               "Overcharge to Refund (KES)": "Overcharge to refund (KES)", "Operational Failure Rate %": "Operational failure rate",
               "Customer Failure Rate %": "Customer-caused failure rate", "Recovered by Retry %": "Operational failures recovered",
               "Unrecovered Operational Failures": "Unrecovered operational failures", "New Customers": "New customers",
               "Dormant Customers": "Dormant customers (90+ days)", "Fee per Active Customer-Month": "Fee per active month (KES)"}
CARD_SUB = {"Successful Transactions": ("m", "Δ Successful Transactions"), "Value Moved (KES)": ("m", "Δ Value Moved"),
            "Fee Income (KES)": ("m", "Δ Fee Income"), "Success Rate %": ("m", "Δ Success Rate"),
            "Active Customers (latest month)": ("m", "Δ Active Customers"), "Fee per Successful Transaction": ("m", "Δ Fee per Transaction"),
            "Active Rate % (latest month)": ("m", "Δ Active Rate"), "Operational Failure Rate %": ("t", "bank or partner caused"),
            "Fee Yield (bps)": ("t", "fees per KES 10,000 moved"), "Fees Lost to Failures (KES)": ("m", "Fees Lost Share Text"),
            "Overcharge to Refund (KES)": ("t", "two fee-engine faults in 2026"),
            "Customer Failure Rate %": ("t", "insufficient funds, wrong PIN, limits"),
            "Recovered by Retry %": ("t", "retried within 30 min and succeeded"),
            "Unrecovered Operational Failures": ("t", "never went through"),
            "New Customers": ("t", "onboarded in the period"), "Dormant Customers": ("m", "Dormant Share Text"),
            "Fee per Active Customer-Month": ("t", "per active customer")}


def card(page, m, pos):
    kind, sub = CARD_SUB.get(m, (None, None))
    cont = panel(CARD_LABELS.get(m, m), subtitle=sub if kind == "t" else None, subtitle_measure=sub if kind == "m" else None,
                 title_size=10, title_colour=INK2)
    cont["divider"] = [{"properties": {"show": lit("true"), "color": col(GOLD), "width": lit("2D")}}]
    v = visual(page, "card_" + m, "card", pos, roles={"Values": [Ms(m)]},
               objects={"categoryLabels": [{"properties": {"show": lit("false")}}],
                        "labels": [{"properties": {"fontSize": lit("22D"), "color": col(NAVY),
                                                   **({"labelDisplayUnits": lit("1000000000D"), "labelPrecision": lit("2L")}
                                                      if m == "Value Moved (KES)" else {"labelDisplayUnits": lit("1D")})}}]},
               container=cont)
    return v


def slicer(page, key, ref, pos, dropdown=True):
    cont = panel(shadow=False)
    cont["padding"] = [{"properties": {"top": lit("2D"), "bottom": lit("2D"), "left": lit("8D"), "right": lit("8D")}}]
    objs = {"header": [{"properties": {"fontColor": col(NAVY), "textSize": lit("9D")}}]}
    if dropdown:
        objs["data"] = [{"properties": {"mode": lit("'Dropdown'")}}]
    return visual(page, key, "slicer", pos, roles={"Values": [ref]}, objects=objs, container=cont)


def header(page, title, question):
    band = textbox(page, "band", [[("TAUSI DIGITAL BANK", 9, GOLD, True), ("   ·   fictional bank, synthetic data", 9, "#b8c4da", False)],
                                  [(title, 20, "#ffffff", True)], [(question, 10, "#dbe3f0", False)]], (0, 0, 1280, 88), bg=NAVY)
    state = lambda i, fill, txt: [{"properties": {"show": lit("true"), "fillColor": col(fill), "transparency": lit("0D")}, "selector": {"id": i}}]
    nav_objs = {"fill": state("default", "#24406e", None) + state("hover", "#2e4f86", None) + state("selected", GOLD, None),
                "text": [{"properties": {"fontColor": col("#ffffff"), "fontSize": lit("10D")}, "selector": {"id": "default"}},
                         {"properties": {"fontColor": col(NAVY), "fontSize": lit("10D"), "bold": lit("true")}, "selector": {"id": "selected"}}],
                "outline": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}},
                            {"properties": {"show": lit("false")}, "selector": {"id": "selected"}}]}
    nav = visual(page, "nav", "pageNavigator", (440, 28, 350, 36), objects=nav_objs,
                 container={"background": [{"properties": {"show": lit("false")}}], "border": [{"properties": {"show": lit("false")}}],
                            "title": [{"properties": {"show": lit("false")}}]})
    return [band, nav,
            slicer(page, "s_date", Co("dim_date", "date"), (800, 10, 244, 68), dropdown=False),
            slicer(page, "s_channel", Co("dim_channel", "channel"), (1050, 10, 108, 68)),
            slicer(page, "s_seg", Co("dim_segment", "segment"), (1164, 10, 108, 68))]


def cards(page, ms, y=98, h=104):
    n = len(ms); w = (1240 - 12 * (n - 1)) // n
    return [card(page, m, (20 + i * (w + 12), y, w, h)) for i, m in enumerate(ms)]


PAPER, KICKER = "#f6f4ef", "#2a78d6"
SERIF, DISPLAY, NUMBERS = "Georgia", "Bahnschrift", "Segoe UI Semibold"


def para(*runs):
    return [{"value": t, "textStyle": {"fontSize": f"{sz}pt", "color": c, **({"fontWeight": "bold"} if b else {}),
                                       **({"fontFamily": f} if f else {})}} for t, sz, c, b, f in runs]


def rich_text(page, key, paragraphs, pos, bg=None, pad=(4, 4, 0, 0)):
    v = visual(page, key, "textbox", pos, objects={"general": [{"properties": {"paragraphs": [{"textRuns": r} for r in paragraphs]}}]})
    vc = {"border": [{"properties": {"show": lit("false")}}],
          "padding": [{"properties": {"top": lit(f"{pad[0]}D"), "bottom": lit(f"{pad[1]}D"), "left": lit(f"{pad[2]}D"), "right": lit(f"{pad[3]}D")}}]}
    vc["background"] = [{"properties": {"show": lit("true" if bg else "false"), **({"color": col(bg), "transparency": lit("0D")} if bg else {})}}]
    v["visual"]["visualContainerObjects"] = vc
    return v


def image(page, key, item, pos):
    return visual(page, key, "image", pos,
                  objects={"general": [{"properties": {"imageUrl": {"expr": {"ResourcePackageItem": {"PackageName": "RegisteredResources", "PackageType": 1, "ItemName": item}}}}}],
                           "imageScaling": [{"properties": {"imageScalingType": lit("'Fill'")}}]},
                  container={"background": [{"properties": {"show": lit("false")}}], "border": [{"properties": {"show": lit("false")}}],
                             "title": [{"properties": {"show": lit("false")}}], "padding": [{"properties": {"top": lit("0D"), "bottom": lit("0D"), "left": lit("0D"), "right": lit("0D")}}]})


def masthead(page):
    band = rich_text(page, "band", [para(("TAUSI DIGITAL BANK", 12, GOLD, True, DISPLAY), ("    fictional bank · synthetic data", 9, "#b8c4da", False, None)),
                                    para(("Transaction intelligence, May 2025 – Oct 2026", 9, "#dbe3f0", False, None))],
                     (0, 0, 1280, 72), bg=NAVY, pad=(12, 4, 20, 0))
    state = lambda i, fill: [{"properties": {"show": lit("true"), "fillColor": col(fill), "transparency": lit("0D")}, "selector": {"id": i}}]
    nav_objs = {"fill": state("default", "#24406e") + state("hover", "#2e4f86") + state("selected", GOLD),
                "text": [{"properties": {"fontColor": col("#ffffff"), "fontSize": lit("10D")}, "selector": {"id": "default"}},
                         {"properties": {"fontColor": col(NAVY), "fontSize": lit("10D"), "bold": lit("true")}, "selector": {"id": "selected"}}],
                "outline": [{"properties": {"show": lit("false")}, "selector": {"id": "default"}},
                            {"properties": {"show": lit("false")}, "selector": {"id": "selected"}}]}
    nav = visual(page, "nav", "pageNavigator", (380, 18, 380, 36), objects=nav_objs,
                 container={"background": [{"properties": {"show": lit("false")}}], "border": [{"properties": {"show": lit("false")}}],
                            "title": [{"properties": {"show": lit("false")}}]})
    return [band, nav,
            slicer(page, "s_date", Co("dim_date", "date"), (800, 7, 244, 58), dropdown=False),
            slicer(page, "s_channel", Co("dim_channel", "channel"), (1050, 7, 108, 58)),
            slicer(page, "s_seg", Co("dim_segment", "segment"), (1164, 7, 108, 58)),
            image(page, "strip", "kitenge_strip.png", (0, 72, 1280, 10))]


def story(page, kicker, headline, standfirst):
    return rich_text(page, "story", [para((kicker.upper(), 9, KICKER, True, None)),
                                     para((headline, 22, NAVY, True, SERIF)),
                                     para((standfirst, 10.5, INK2, False, None))], (20, 90, 840, 104), pad=(2, 2, 0, 0))


def tile(page, m, y, label, sub=None, sub_measure=None):
    cont = panel(label, subtitle=sub, subtitle_measure=sub_measure, title_size=10, title_colour=INK2, shadow=False)
    cont["divider"] = [{"properties": {"show": lit("true"), "color": col(GOLD), "width": lit("2D")}}]
    cont["padding"] = [{"properties": {"top": lit("6D"), "bottom": lit("0D"), "left": lit("12D"), "right": lit("10D")}}]
    units = {"labelDisplayUnits": lit("1000000D"), "labelPrecision": lit("1L")} if m in ("Fee Income (KES)",) else {"labelDisplayUnits": lit("1D")}
    return visual(page, "tile_" + m, "card", (880, y, 380, 96), roles={"Values": [Ms(m)]},
                  objects={"categoryLabels": [{"properties": {"show": lit("false")}}],
                           "labels": [{"properties": {"fontSize": lit("24D"), "color": col(NAVY), "fontFamily": lit(f"'{NUMBERS}'"), **units}}]},
                  container=cont)


def todo(page, items, y, h):
    paras = [para(("WHAT WE'D DO", 10, GOLD, True, DISPLAY))] + [para(("›  " + t, 11, "#ffffff", False, None)) for t in items]
    return rich_text(page, "todo", paras, (880, y, 380, h), bg=NAVY, pad=(14, 10, 16, 12))


def xline(day, label, idx, colour=INK2):
    return {"properties": {"show": lit("true"), "displayName": lit(f"'{label}'"),
                           "value": {"expr": {"Literal": {"Value": f"datetime'{day}T00:00:00'"}}},
                           "lineColor": col(colour), "style": lit("'dashed'"), "transparency": lit("20D"), "position": lit("'front'"),
                           "dataLabelShow": lit("true"), "dataLabelColor": col(colour), "dataLabelText": lit("'Name'"),
                           "dataLabelHorizontalPosition": lit("'right'"), "dataLabelVerticalPosition": lit("'above'")},
            "selector": {"id": str(idx)}}


HERO, LOW1, LOW2 = (20, 200, 840, 268), (20, 478, 414, 234), (446, 478, 414, 234)


def build_pages():
    pages = []
    MS = Co("dim_date", "month_start")
    CH = ("dim_channel", "channel")
    chans = ["Mobile App", "USSD", "Agent", "ATM", "Internet Banking", "Wallet Link"]

    p = hid("p1")
    v = masthead(p)
    v.append(story(p, "Overview", "Growing fast while the market stands still",
                   "Successful transactions rose 78% in 18 months while Kenya's agent market stayed flat. Fee income kept pace at about "
                   "KES 26.50 a transaction, but the money moved is not the money earned."))
    v.append(visual(p, "mkt", "lineChart", HERO,
                    roles={"Category": [Co("market_context", "month")], "Y": [Ms("Bank Volume Index"), Ms("Market Volume Index (CBK)")]},
                    title="Tausi's volume vs the national market", subtitle="Index, May 2025 = 100 · market = CBK agent transactions (real data)",
                    sort=(Co("market_context", "month"), "Ascending"),
                    objects={**measure_series_fill({"Bank Volume Index": BLUE, "Market Volume Index (CBK)": GREY}),
                             "xAxisReferenceLine": [xline("2026-02-01", "Free small app transfers", 1)]}))
    v.append(visual(p, "type", "clusteredBarChart", LOW1,
                    roles={"Category": [Co("fact_txn_daily", "transaction_type")], "Y": [Ms("Share of Value"), Ms("Share of Fees")]},
                    title="Value moved is not revenue", subtitle="Share of value (blue) vs share of fees (orange)",
                    sort=(Ms("Share of Value"), "Descending"), objects=measure_series_fill({"Share of Value": BLUE, "Share of Fees": ORANGE})))
    v.append(visual(p, "fee", "lineChart", LOW2, roles={"Category": [MS], "Y": [Ms("Fee Income (KES)")]},
                    title="Fee income per month (KES)", sort=(MS, "Ascending"), objects=measure_series_fill({"Fee Income (KES)": BLUE})))
    v += [tile(p, "Successful Transactions", 90, "Successful transactions", sub_measure="Δ Successful Transactions"),
          tile(p, "Fee Income (KES)", 192, "Fee income (KES millions)", sub_measure="Δ Fee Income"),
          tile(p, "Active Customers (latest month)", 294, "Active customers, Oct 2026", sub_measure="Δ Active Customers"),
          todo(p, ["Refund the KES 96k fee overcharge and reconcile fees daily",
                   "Fix the two biggest leaks: USSD at the evening peak and agent float on paydays",
                   "Keep moving small transfers from USSD to the app"], 498, 214),
          tile(p, "Success Rate %", 396, "Success rate", sub_measure="Δ Success Rate")]
    pages.append((p, "Overview", v))

    p = hid("p2")
    v = masthead(p)
    v.append(story(p, "Channels & fees", "Customers are leaving USSD for the app",
                   "The app carries 43% of transactions and now earns the most fees. Agents carry 12% of volume but 20% of fees. "
                   "Two fee-engine faults charged customers KES 96k they did not owe."))
    v.append(visual(p, "mix", "lineChart", HERO, roles={"Category": [MS], "Series": [Co(*CH)], "Y": [Ms("Share of Transactions")]},
                    title="Share of successful transactions by channel", subtitle="App in blue, USSD in orange, other channels grey",
                    sort=(MS, "Ascending"),
                    objects={**series_fill(*CH, {"Mobile App": BLUE, "USSD": ORANGE}, GREY, chans),
                             "xAxisReferenceLine": [xline("2026-02-01", "Small app-to-wallet transfers made free", 1)]}))
    v.append(visual(p, "fee_ch", "clusteredBarChart", LOW1, roles={"Category": [Co(*CH)], "Y": [Ms("Fee Income (KES)")]},
                    title="The app now earns the most fees", subtitle="Fee income by channel, KES",
                    sort=(Ms("Fee Income (KES)"), "Descending"), objects=measure_fill("Colour Fee Channel")))
    v.append(visual(p, "yield_ch", "clusteredBarChart", LOW2, roles={"Category": [Co(*CH)], "Y": [Ms("Fee Yield (bps)")]},
                    title="Cash channels earn the most per shilling", subtitle="Fee yield, basis points of value moved",
                    sort=(Ms("Fee Yield (bps)"), "Descending"), objects=measure_fill("Colour Yield Channel")))
    v += [tile(p, "Fee Income (KES)", 90, "Fee income (KES millions)", sub_measure="Δ Fee Income"),
          tile(p, "Fees Lost to Failures (KES)", 192, "Fees lost to failures (KES)", sub_measure="Fees Lost Share Text"),
          tile(p, "Overcharge to Refund (KES)", 294, "Overcharge to refund (KES)", sub="two fee-engine faults in 2026"),
          todo(p, ["Refund 4,051 overcharged transactions and add a daily tariff check",
                   "Keep the wallet-transfer waiver: it moved traffic off USSD for about KES 62k a month",
                   "Back agents with float on paydays: they earn a fifth of all fees"], 498, 214),
          tile(p, "Fee Yield (bps)", 396, "Fee yield (bps of value)", sub="fees per KES 10,000 moved")]
    pages.append((p, "Channels & fees", v))

    p = hid("p3")
    v = masthead(p)
    v.append(story(p, "Reliability", "USSD fails customers when they need it most",
                   "One in twenty USSD attempts fails between 6 and 9 pm, and agents run out of cash on paydays. "
                   "Half of failures are recovered by a retry; 16,822 never went through. Three outages were never logged."))
    v.append(visual(p, "daily", "lineChart", HERO, roles={"Category": [Co("dim_date", "date")], "Y": [Ms("Operational Failure Rate %")]},
                    title="Daily operational failure rate, all channels", subtitle="Dashed lines: events found in the data",
                    sort=(Co("dim_date", "date"), "Ascending"),
                    objects={**measure_series_fill({"Operational Failure Rate %": BLUE}),
                             "xAxisReferenceLine": [xline("2025-10-24", "App outage (not logged)", 1, ORANGE),
                                                    xline("2026-03-10", "Faulty app release", 2, ORANGE),
                                                    xline("2026-05-29", "USSD outage (not logged)", 3, ORANGE)]}))
    v.append(visual(p, "op_hr", "lineChart", LOW1,
                    roles={"Category": [Co("fact_channel_hour", "hour_of_day")], "Series": [Co(*CH)], "Y": [Ms("Hourly Operational Failure Rate %")]},
                    title="USSD failures double after 6 pm", subtitle="Failure rate by hour · USSD orange, app blue",
                    sort=(Co("fact_channel_hour", "hour_of_day"), "Ascending"),
                    objects={**series_fill(*CH, {"Mobile App": BLUE, "USSD": ORANGE}, GREY, chans),
                             "legend": [{"properties": {"show": lit("false")}}]}))
    v.append(visual(p, "reason", "clusteredBarChart", LOW2, roles={"Category": [Co("fact_failures", "failure_reason")], "Y": [Ms("Failures")]},
                    title="Half of failures are the customer's own", subtitle="Orange: the bank's to fix · grey: customer-caused",
                    sort=(Ms("Failures"), "Descending"), objects=measure_fill("Colour Failure Owner")))
    status = visual(p, "status", "tableEx", (880, 90, 380, 210),
                    roles={"Values": [Co(*CH), Ms("Operational Failure Rate %"), Ms("Channel Status")]},
                    title="Channel status", subtitle="Watch = operational failure rate 2.5% or more",
                    sort=(Ms("Operational Failure Rate %"), "Descending"),
                    objects={"columnHeaders": [{"properties": {"fontColor": col(NAVY), "bold": lit("true")}}],
                             "values": [{"properties": {"fontColor": mcol("Colour Channel Status")},
                                         "selector": {"metadata": "_Measures.Channel Status"}}],
                             "total": [{"properties": {"totals": lit("false")}}]})
    v += [status,
          tile(p, "Unrecovered Operational Failures", 308, "Transactions that never went through", sub="operational failures not rescued by a retry"),
          tile(p, "Recovered by Retry %", 410, "Failures rescued by a retry", sub="retried within 30 minutes and succeeded"),
          todo(p, ["Agree evening USSD capacity with the network partner",
                   "Float alerts for top agents from the 24th of each month",
                   "Staged app releases with automatic rollback",
                   "Log every outage; review anomaly flags weekly"], 512, 200)]
    pages.append((p, "Reliability", v))

    p = hid("p4")
    v = masthead(p)
    v.append(story(p, "Customers", "One in five customers has gone quiet",
                   "5,591 customers have not completed a transaction in 90 days, mostly Youth and Mass Retail. "
                   "Customers who hit two or more failures in a month are about twice as likely to go quiet the next month."))
    v.append(visual(p, "dorm_seg", "clusteredBarChart", HERO, roles={"Category": [Co("dim_segment", "segment")], "Y": [Ms("Dormant %")]},
                    title="Dormant share by segment", subtitle="No successful transaction in the 90 days to 31 Oct 2026 · customers who joined before Aug 2026",
                    sort=(Ms("Dormant %"), "Descending"), objects=measure_fill("Colour Segment Focus")))
    v.append(visual(p, "act", "lineChart", LOW1, roles={"Category": [MS], "Y": [Ms("Active Customers")]},
                    title="Active customers per month", sort=(MS, "Ascending"), objects=measure_series_fill({"Active Customers": BLUE})))
    v.append(visual(p, "rate_seg", "clusteredBarChart", LOW2, roles={"Category": [Co("dim_segment", "segment")], "Y": [Ms("Active Rate %")]},
                    title="Average monthly active rate", sort=(Ms("Active Rate %"), "Descending"), objects=measure_fill("Colour Segment Focus")))
    v += [tile(p, "Active Customers (latest month)", 90, "Active customers, Oct 2026", sub_measure="Δ Active Customers"),
          tile(p, "Dormant Customers", 192, "Dormant customers (90+ days)", sub_measure="Dormant Share Text"),
          tile(p, "Active Rate % (latest month)", 294, "Active rate, Oct 2026", sub_measure="Δ Active Rate"),
          todo(p, ["Reactivation offer for dormant Youth and Mass Retail customers",
                   "Proactive message or fee refund after 2+ failed transactions in a month",
                   "Measure both with a random hold-out group"], 498, 214),
          tile(p, "New Customers", 396, "New customers", sub="onboarded in the period")]
    pages.append((p, "Customers", v))
    return pages


def write_report():
    d = RP / "definition"
    if (d / "pages").exists():
        shutil.rmtree(d / "pages")
    (d / "pages").mkdir(parents=True)
    tpl = PB / "_template"
    shutil.copytree(tpl / "StaticResources", RP / "StaticResources", dirs_exist_ok=True)
    (RP / "StaticResources" / "RegisteredResources").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PB / "theme.json", RP / "StaticResources" / "RegisteredResources" / "theme.json")
    shutil.copyfile(PB / "kitenge_strip.png", RP / "StaticResources" / "RegisteredResources" / "kitenge_strip.png")
    shutil.copyfile(tpl / "version.json", d / "version.json")
    report = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/1.2.0/schema.json",
              "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.60", "type": "SharedResources"},
                                  "customTheme": {"name": "theme.json", "reportVersionAtImport": "5.60", "type": "RegisteredResources"}},
              "layoutOptimization": "None",
              "resourcePackages": [{"name": "SharedResources", "type": "SharedResources",
                                    "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
                                   {"name": "RegisteredResources", "type": "RegisteredResources",
                                    "items": [{"name": "theme.json", "path": "theme.json", "type": "CustomTheme"},
                                              {"name": "kitenge_strip.png", "path": "kitenge_strip.png", "type": "Image"}]}],
              "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                           "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}}
    (d / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    pages = build_pages()
    for p, disp, vs in pages:
        pd_ = d / "pages" / p
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.2.0/schema.json",
                                                   "name": p, "displayName": disp, "displayOption": "FitToPage", "height": 720, "width": 1280,
                                                   "objects": {"background": [{"properties": {"color": col(PAPER), "transparency": lit("0D")}}],
                                                               "outspace": [{"properties": {"color": col(PAPER), "transparency": lit("0D")}}]}}, indent=2), encoding="utf-8")
        for i, vj in enumerate(vs):
            vj["position"]["tabOrder"] = i; vj["position"]["z"] = i
            (pd_ / "visuals" / vj["name"]).mkdir()
            (pd_ / "visuals" / vj["name"] / "visual.json").write_text(json.dumps(vj, indent=2), encoding="utf-8")
    (d / "pages" / "pages.json").write_text(json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
                                                        "pageOrder": [p for p, _, _ in pages], "activePageName": pages[0][0]}, indent=2), encoding="utf-8")
    (RP / "definition.pbir").write_text(json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}, "byConnection": None}}, indent=2), encoding="utf-8")
    (RP / ".platform").write_text(json.dumps({"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                                              "metadata": {"type": "Report", "displayName": NAME},
                                              "config": {"version": "2.0", "logicalId": tag("rp-logical")}}, indent=2), encoding="utf-8")
    (PB / f"{NAME}.pbip").write_text(json.dumps({"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                                  "settings": {"enableAutoRecovery": True}}, indent=2), encoding="utf-8")
    return sum(len(v) for _, _, v in pages), len(pages)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-folder", default=str(ROOT / "data" / "processed" / "powerbi") + "\\")
    a = ap.parse_args()
    write_model(a.data_folder)
    nv, np_ = write_report()
    print(f"model: {len(TABLES) + 1} tables, {len(RELS)} relationships, {len(MEASURES)} measures; report: {np_} pages, {nv} visuals")
