#!/usr/bin/env python3
"""Send the daily בִּינָה Binah digest email, built from data.js.

Stdlib only. Sends via Gmail SMTP with an app password.

Env vars:
  GMAIL_APP_PASSWORD  required to send (Google Account -> Security -> App passwords)
  GMAIL_USER          sender Gmail account (required to send)
  DIGEST_TO           recipient        (default: GMAIL_USER)
  DASHBOARD_URL       link in the footer (optional, e.g. GitHub Pages URL)

Usage:
  python3 send_digest.py                  # build + send
  python3 send_digest.py --preview out.html   # build only, write HTML to a file
"""

import html as html_mod
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("America/New_York")
NEW_WINDOW_HOURS = 24

# Public-facing contact (subscribe/unsubscribe target). Assembled from parts
# so the address never appears whole in this public repo; a redirect delivers
# it to the sending inbox. The actual SMTP account comes from GMAIL_USER.
PUBLIC_EMAIL = ".".join(("rabbi", "dan")) + "@" + ".".join(("medw", "in"))

# Gmail-safe styling: inline styles, web-safe fonts, light theme only.
INK = "#1E2A3A"
SOFT = "#4A5568"
FAINT = "#8B93A1"
GOLD = "#B07A1E"
PAPER = "#FAF5EA"
CARD = "#FFFDF7"
RULE = "#D8CDB6"
SERIF = "Georgia, serif"  # no quoted names — styles use single-quoted attributes
SANS = "Arial, Helvetica, sans-serif"


def esc(s):
    return html_mod.escape(str(s), quote=True)


def load_data():
    raw = (HERE / "data.js").read_text(encoding="utf-8")
    return json.loads(re.sub(r"^window\.NEWS_DATA = |;\s*$", "", raw.strip()))


def item_row(i, with_why=False):
    summary = i.get("aiSummary") or i.get("summary") or ""
    why = ""
    if with_why and i.get("whyMatters"):
        why = ("<div style='font-family:%s;font-size:11px;letter-spacing:2px;color:%s;"
               "margin:8px 0 2px;'>WHY THIS MATTERS</div>" % (SANS, GOLD))
        why += "".join(
            "<div style='color:%s;font-size:13px;line-height:1.5;padding-left:14px;'>"
            "<span style='color:%s;'>&#9656;</span> %s</div>" % (SOFT, GOLD, esc(w))
            for w in i["whyMatters"]
        )
    pod = (" &nbsp;<span style='color:%s;font-size:11px;'>&#9654; PODCAST</span>" % GOLD
           if i.get("category") == "podcast" else "")
    return (
        "<div style='background:%s;border:1px solid %s;border-radius:8px;"
        "padding:14px 16px;margin:0 0 10px;'>"
        "<div style='font-family:%s;font-size:11px;letter-spacing:1.5px;color:%s;'>%s%s</div>"
        "<div style='font-family:%s;font-size:16px;font-weight:bold;line-height:1.35;margin:4px 0 6px;'>"
        "<a href='%s' style='color:%s;text-decoration:none;'>%s</a></div>"
        "<div style='color:%s;font-size:13.5px;line-height:1.5;'>%s</div>%s</div>"
        % (CARD, RULE, SANS, FAINT, esc(i["source"].upper()), pod,
           SERIF, esc(i["link"]), INK, esc(i["title"]),
           SOFT, esc(summary), why)
    )


def section_head(heb, latin):
    return (
        "<div style='font-family:%s;font-size:20px;font-weight:bold;color:%s;"
        "margin:28px 0 12px;border-bottom:2px solid %s;padding-bottom:6px;'>"
        "<span style='color:%s;'>%s</span> %s</div>" % (SERIF, INK, RULE, GOLD, esc(heb), esc(latin))
    )


def model_display(model_id):
    """claude-haiku-4-5 -> 'Claude Haiku 4.5'"""
    parts = model_id.replace("claude-", "").split("-")
    name = parts[0].capitalize()
    version = ".".join(p for p in parts[1:] if p.isdigit())
    return ("Claude %s %s" % (name, version)).strip()


def build_html(data):
    now = datetime.now(TZ)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=NEW_WINDOW_HOURS)).isoformat()
    items = data["items"]
    by_link = {i["link"]: i for i in items}
    fresh = [i for i in items if (i.get("date") or "") >= cutoff]
    url = os.environ.get("DASHBOARD_URL", "").strip()

    parts = []
    dash_link = (
        "<div style='margin-top:8px;'><a href='%s' style='font-family:%s;font-size:12.5px;"
        "color:%s;text-decoration:none;'>Open the full dashboard &rarr;</a></div>" % (esc(url), SANS, GOLD)
    ) if url else ""
    parts.append(
        "<div style='text-align:center;padding:26px 0 14px;border-bottom:3px double %s;'>"
        "<div style='font-family:%s;font-size:11px;letter-spacing:3px;color:%s;'>A DAILY DIGEST OF ARTIFICIAL INTELLIGENCE</div>"
        "<div style='font-family:%s;font-size:40px;font-weight:bold;color:%s;margin:6px 0 2px;'>"
        "<span style='color:%s;'>בִּינָה</span> Binah</div>"
        "<div style='font-family:%s;font-size:13px;color:%s;font-style:italic;'>%s</div>%s</div>"
        % (RULE, SANS, FAINT, SERIF, INK, GOLD, SERIF, SOFT,
           esc(now.strftime("%A, %B %-d, %Y")), dash_link)
    )

    hl = data.get("highlights")
    if hl and hl.get("bullets"):
        parts.append(section_head("הָעִקָּר", "HaIkar — What You Need to Know"))
        rows = []
        for n, b in enumerate(hl["bullets"], 1):
            text = b if isinstance(b, str) else b.get("text", "")
            links = [] if isinstance(b, str) else [by_link[l] for l in b.get("links", []) if l in by_link]
            chips = ""
            if links:
                chips = "<div style='margin-top:5px;'>" + " &nbsp;·&nbsp; ".join(
                    "<a href='%s' style='font-family:%s;font-size:12px;color:%s;text-decoration:none;'>%s <span style='color:%s;'>(%s)</span></a>"
                    % (esc(i["link"]), SANS, GOLD, esc(i["title"][:70]), FAINT, esc(i["source"]))
                    for i in links
                ) + "</div>"
            rows.append(
                "<tr><td width='34' style='font-family:%s;font-size:22px;font-weight:bold;color:%s;"
                "padding:10px 14px 10px 2px;vertical-align:top;'>%d</td>"
                "<td style='padding:10px 0;border-bottom:1px solid %s;color:%s;"
                "font-size:15px;line-height:1.55;'>%s%s</td></tr>"
                % (SERIF, GOLD, n, RULE, INK, esc(text), chips)
            )
        parts.append("<table cellpadding='0' cellspacing='0' style='width:100%%;font-family:%s;'>%s</table>" % (SERIF, "".join(rows)))

    pods = [i for i in fresh if i["category"] == "podcast"]
    if pods:
        parts.append(section_head("הַאֲזָנָה", "Ha'azanah — Worth a Listen"))
        parts.append("".join(
            "<div style='font-family:%s;font-size:14px;line-height:1.7;color:%s;'>&#9654;&nbsp; "
            "<a href='%s' style='color:%s;text-decoration:none;font-weight:bold;'>%s</a>"
            " <span style='color:%s;font-size:12px;'>· %s</span></div>"
            % (SERIF, INK, esc(i["link"]), INK, esc(i["title"]), FAINT, esc(i["source"]))
            for i in pods[:6]
        ))

    religion = [i for i in fresh if i.get("religionScore", 0) >= 3]
    if religion:
        parts.append(section_head("בִּינָה וֶאֱמוּנָה", "Binah Ve'emunah — AI & Religion"))
        parts.extend(item_row(i, with_why=True) for i in religion[:5])

    hl_links = set()
    if hl:
        for b in hl.get("bullets", []):
            if not isinstance(b, str):
                hl_links.update(b.get("links", []))
    stories = [i for i in fresh if i["category"] != "podcast"
               and i["link"] not in hl_links and i.get("religionScore", 0) < 3]
    if stories:
        parts.append(section_head("עוֹד חֲדָשׁוֹת", "Od Chadashot — More From the Last 24 Hours"))
        parts.extend(item_row(i) for i in stories[:8])

    from enrich_news import MODEL as ENRICH_MODEL
    unsub = ("mailto:%s?subject=Unsubscribe%%20from%%20Binah&body=Please%%20remove%%20me%%20"
             "from%%20the%%20Binah%%20digest." % PUBLIC_EMAIL)
    parts.append(
        "<div style='text-align:center;font-family:%s;font-size:11.5px;color:%s;"
        "border-top:3px double %s;margin-top:26px;padding:16px 0;'>"
        "<span style='color:%s;'>בִּינָה</span> Binah · %d stories tracked · Curated by %s"
        " · <a href='%s' style='color:%s;'>Unsubscribe</a></div>"
        % (SANS, FAINT, RULE, GOLD, len(items), esc(model_display(ENRICH_MODEL)),
           esc(unsub), FAINT)
    )

    return (
        "<body style='margin:0;padding:0;background:%s;'>"
        "<div style='max-width:640px;margin:0 auto;padding:8px 20px 30px;background:%s;color:%s;'>%s</div></body>"
        % (PAPER, PAPER, INK, "".join(parts))
    )


def subject_line(data):
    now = datetime.now(TZ)
    hl = data.get("highlights")
    teaser = ""
    if hl and hl.get("bullets"):
        b = hl["bullets"][0]
        text = b if isinstance(b, str) else b.get("text", "")
        teaser = " — " + (text[:60].rsplit(" ", 1)[0] + "…" if len(text) > 60 else text)
    return "בִּינָה Binah · %s%s" % (now.strftime("%b %-d"), teaser)


def main():
    data = load_data()
    body = build_html(data)

    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        Path(sys.argv[2]).write_text("<!doctype html><meta charset='utf-8'>" + body, encoding="utf-8")
        print("Preview written to", sys.argv[2])
        return

    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not password:
        print("GMAIL_APP_PASSWORD not set — skipping digest send.")
        return
    user = os.environ.get("GMAIL_USER", "").strip()
    if not user:
        print("GMAIL_USER not set — skipping digest send.")
        return
    # DIGEST_TO accepts a comma-separated list; extra recipients ride as BCC
    # so subscribers never see each other's addresses.
    raw_to = os.environ.get("DIGEST_TO", "").strip() or user
    recipients = [a.strip() for a in raw_to.split(",") if a.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject_line(data)
    msg["From"] = "בִּינָה Binah <%s>" % user
    msg["To"] = user
    # Lets Gmail/clients surface their native "Unsubscribe" affordance;
    # requests arrive in the sender's inbox — remove the address from DIGEST_TO.
    msg["List-Unsubscribe"] = "<mailto:%s?subject=Unsubscribe%%20from%%20Binah>" % PUBLIC_EMAIL
    msg.attach(MIMEText("Your daily Binah digest — view in an HTML mail client.", "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, password)
        s.sendmail(user, recipients, msg.as_string())
    print("Digest sent to %d recipient(s)." % len(recipients))


if __name__ == "__main__":
    main()
