import os
import base64
import re
import asyncio
import aiohttp
import requests
import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from urllib.parse import urlencode
from datetime import datetime
from urllib.parse import urlencode



app = Flask(__name__)
app.secret_key = "supersecretkey"  # session + flash

@app.route('/intro')
def intro():
    return render_template('intro.html')

@app.route('/blog/why')
def blog_why():
    return render_template('blog.html')

@app.route('/case')
def case():
    return render_template('case.html')

@app.route('/doc')
def doc():
    return render_template('doc.html')

@app.route('/disclaimer')
def disclaimer():
    return render_template('disclaimer.html')

@app.context_processor
def inject_session():
    return dict(session=session)

@app.route("/oauth/linkedin/logout")
def li_logout():
    session.pop("li_token", None)
    return ("", 204)  # return no content for fetch()

@app.route("/oauth/twitter/logout")
def tw_logout():
    session.pop("tw_token", None)
    return ("", 204)


# ---------------- Config ----------------
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "1718598702202188")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "ba989ffd85f6244abb11e80f4bcd5064")
FACEBOOK_REDIRECT_URI = os.getenv("FACEBOOK_REDIRECT_URI", "http://localhost:8000/oauth/facebook/callback")

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "7812hhk04l7tik")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "WPL_AP1.EzmF4sKA4W89UiIT.4TGhmg==")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/oauth/linkedin/callback")

TWITTER_CLIENT_ID = os.getenv("TWITTER_CLIENT_ID", "N2NkMEkwMmFtaVBCME5Iem05cWs6MTpjaQ")
TWITTER_CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET", "FsEgafSt737aJwbCco3QJk3vqXq9lpq19LNwkgcJGes8yZqET3")
TWITTER_REDIRECT_URI = os.getenv("TWITTER_REDIRECT_URI", "http://localhost:8000/oauth/twitter/callback")

GRAPH_URL = "https://graph.facebook.com/v18.0"

# ---------------- Globals ----------------
user_tokens = {}
current_pages = {}

# ---------------- Helpers ----------------
def format_date(raw):
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return raw

# ---------------- Compliance Checker ----------------

TRIGGER_PHRASES = [
    # Guarantees
    "guarantee", "guaranteed", "guarantees",
    "risk free", "zero risk", "no risk", "safe investment",
    "can't lose", "never lose", "foolproof", "bulletproof",
    "100% success rate", "success guaranteed",
    "double your money", "triple your money",
    "instant profits", "automatic profits", "steady profits",
    "sure thing", "hot tip", "slam dunk",

    # Get Rich Quick
    "get rich quick", "easy money", "quick cash",
    "explosive growth", "skyrocket", "moonshot",
    "massive upside", "unlimited upside",
    "instant wealth", "overnight success",
    "fast track to wealth", "shortcut to wealth",
    "double-digit returns", "triple-digit returns",
    "outperform the market", "beat the market",

    # Insider or Advantage
    "insider information", "secret strategy", "exclusive access",
    "private deal", "confidential tip", "non-public info",
    "special opportunity", "invitation only", "loophole", "hidden secret",

    # Absolutes
    "best investment", "top pick", "#1 choice",
    "once-in-a-lifetime", "can’t-miss", "unbeatable",
    "always wins", "never fails", "permanent gains",

    # Unlicensed Advice
    "buy this now", "you should invest", "everyone must own",
    "move your money", "this stock will definitely"
]

def check_compliance(text):
    text_lower = text.lower()
    violations = []
    for phrase in TRIGGER_PHRASES:
        pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
        if re.search(pattern, text_lower):
            violations.append(phrase)
    return violations




# ---------------- Facebook ----------------
@app.route("/oauth/facebook/login")
def fb_login():
    fb_oauth_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth"
        f"?client_id={FACEBOOK_APP_ID}"
        f"&redirect_uri={FACEBOOK_REDIRECT_URI}"
        f"&scope=pages_read_engagement,pages_read_user_content,"
        f"pages_manage_metadata,pages_manage_posts,pages_show_list"
    )
    return redirect(fb_oauth_url)

@app.route("/oauth/facebook/callback")
def fb_callback():
    code = request.args.get("code")
    if not code:
        flash("❌ No code returned from Facebook", "error")
        return redirect(url_for("index"))

    token_url = f"{GRAPH_URL}/oauth/access_token"
    params = {
        "client_id": FACEBOOK_APP_ID,
        "redirect_uri": FACEBOOK_REDIRECT_URI,
        "client_secret": FACEBOOK_APP_SECRET,
        "code": code,
    }
    res = requests.get(token_url, params=params).json()
    access_token = res.get("access_token")

    if not access_token:
        flash(f"❌ Failed to get token: {res}", "error")
        return redirect(url_for("index"))

    # Exchange for long-lived
    long_token_url = f"{GRAPH_URL}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": FACEBOOK_APP_ID,
        "client_secret": FACEBOOK_APP_SECRET,
        "fb_exchange_token": access_token,
    }
    res2 = requests.get(long_token_url, params=params).json()
    long_token = res2.get("access_token", access_token)

    user_tokens["current_user"] = long_token
    flash("✅ Facebook login successful", "success")
    return redirect(url_for("index"))

def resolve_page_id_and_token(user_input, user_token):
    cleaned = user_input.strip()
    if "facebook.com" in cleaned:
        match = re.search(r"facebook\.com/([^/?#]+)", cleaned)
        if match:
            cleaned = match.group(1).strip("/")
    if not cleaned.isdigit():
        url = f"{GRAPH_URL}/{cleaned}"
        params = {"fields": "id,name", "access_token": user_token}
        res = requests.get(url, params=params).json()
        if "id" in res:
            cleaned = res["id"]

    url = f"{GRAPH_URL}/me/accounts"
    params = {"access_token": user_token}
    res = requests.get(url, params=params).json()
    for page in res.get("data", []):
        if page["id"] == cleaned:
            return page["id"], page["access_token"]
    return cleaned, user_token

def fb_fetch_posts(page_id, token):
    posts = []
    url = f"{GRAPH_URL}/{page_id}/posts?access_token={token}&limit=25"
    while url:
        res = requests.get(url).json()
        for p in res.get("data", []):
            message = p.get("message", p.get("story", "[No text]"))
            violations = check_compliance(message)  # ✅ run compliance check
            posts.append({
                "id": p.get("id"),
                "message": message,
                "created_time": format_date(p.get("created_time", "")),
                "platform": "facebook",
                "violations": violations   # ✅ attach violations
            })
        url = res.get("paging", {}).get("next")
    return posts


@app.route("/facebook/collect", methods=["POST"])
def fb_collect():
    if "current_user" not in user_tokens:
        flash("❌ Please log in with Facebook first.", "error")
        return redirect(url_for("index"))

    user_token = user_tokens["current_user"]
    raw_ids = request.form.get("page_id", "").split(",")
    resolved_ids_tokens = [resolve_page_id_and_token(uid, user_token) for uid in raw_ids if uid.strip()]

    for pid, token in resolved_ids_tokens:
        current_pages[pid] = token

    all_posts = {}
    for pid, token in resolved_ids_tokens:
        all_posts[pid] = fb_fetch_posts(pid, token)

    session["fb_last_page"] = raw_ids[0]
    return render_template("index.html", all_posts=all_posts, active_tab="facebook")

@app.route("/facebook/create", methods=["POST"])
def fb_create():
    page_id = request.form.get("page_id")
    message = request.form.get("message")
    token = current_pages.get(page_id)

    if not token:
        flash("❌ Missing Page token.", "error")
        return redirect(url_for("index"))

    violations = check_compliance(message)  # ✅ scan before posting

    url = f"{GRAPH_URL}/{page_id}/feed"
    res = requests.post(url, params={"access_token": token, "message": message}).json()

    if "id" in res:
        flash("✅ Post published successfully.", "success")
        if violations:
            flash(f"⚠️ Compliance issues detected: {', '.join(violations)}", "error")
    else:
        flash(f"❌ Failed to publish post: {res}", "error")

    return redirect(url_for("fb_collect"), code=307)


@app.route("/facebook/delete", methods=["POST"])
def fb_delete():
    page_id = request.form.get("page_id")
    post_id = request.form.get("post_id")
    token = current_pages.get(page_id)

    if not token:
        flash("❌ Missing Page token.", "error")
        return redirect(url_for("index"))

    url = f"{GRAPH_URL}/{post_id}"
    res = requests.delete(url, params={"access_token": token}).json()

    if res.get("success"):
        flash("✅ Post deleted successfully.", "success")
    else:
        flash(f"❌ Failed to delete post: {res}", "error")

    return redirect(url_for("fb_collect"), code=307)

# ---------------- Mock LinkedIn ----------------
mock_li_posts = [
    {
        "id": "demo-1",
        "message": "We aim to help our clients create a portfolio that aligns with their needs.",
        "created_time": "Sep 1, 2025 09:00 AM",
    },
    {
        "id": "demo-2",
        "message": "The stock market has been volatile lately—try implementing a monitoring schedule, this may help reduce unease.",
        "created_time": "Sep 2, 2025 11:30 AM",
    },
]

@app.route("/oauth/linkedin/login")
def li_mock_login():
    session["li_token"] = "mock_linkedin_token"
    flash("✅ LinkedIn login successful (demo)", "success")
    return redirect(url_for("li_collect"))

@app.route("/linkedin/collect")
def li_collect():
    if "li_token" not in session:
        flash("❌ Please log in with LinkedIn first.", "error")
        return redirect(url_for("index", active_tab="linkedin"))

    flagged_posts = []
    for p in mock_li_posts:
        p["violations"] = check_compliance(p["message"])
        flagged_posts.append(p)

    return render_template("index.html", li_posts=flagged_posts, active_tab="linkedin")

@app.route("/linkedin/create", methods=["POST"])
def li_create():
    if "li_token" not in session:
        flash("❌ Please login to LinkedIn first.", "error")
        return redirect(url_for("index", active_tab="linkedin"))

    msg = request.form.get("message")
    new_post = {
        "id": str(len(mock_li_posts) + 1),
        "message": msg,
        "created_time": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "violations": check_compliance(msg),
    }
    mock_li_posts.insert(0, new_post)

    flash("✅ LinkedIn post published (demo)", "success")
    return redirect(url_for("li_collect"))

@app.route("/linkedin/delete", methods=["POST"])
def li_delete():
    if "li_token" not in session:
        flash("❌ Please log in with LinkedIn first.", "error")
        return redirect(url_for("index", active_tab="linkedin"))

    post_id = request.form.get("post_id")
    global mock_li_posts
    mock_li_posts = [p for p in mock_li_posts if p["id"] != post_id]

    flash("🗑️ LinkedIn post deleted (demo)", "success")
    return redirect(url_for("li_collect"))


# ---------------- Mock Twitter ----------------
mock_twitter_posts = [
    {"id": "t1", "message": "Guaranteed returns with this stock! 🚀", "created_time": "Sep 01, 2025 10:00 AM"},
    {"id": "t2", "message": "This is a completely safe investment for retirement.", "created_time": "Sep 02, 2025 09:30 AM"},
    {"id": "t3", "message": "Our goal is to help you identify suitable investment options for your objectives.", "created_time": "Sep 02, 2025 11:00 AM"},
]

@app.route("/oauth/twitter/login")
def tw_mock_login():
    session["tw_token"] = "mock_twitter_token"
    flash("✅ Twitter login successful (demo)", "success")
    return redirect(url_for("tw_collect"))

@app.route("/twitter/collect")
def tw_collect():
    if "tw_token" not in session:
        flash("❌ Please log in with Twitter first.", "error")
        return redirect(url_for("index", active_tab="twitter"))

    flagged_posts = []
    for p in mock_twitter_posts:
        p["violations"] = check_compliance(p["message"])
        flagged_posts.append(p)

    return render_template("index.html", tw_posts=flagged_posts, active_tab="twitter")

@app.route("/twitter/create", methods=["POST"])
def tw_create():
    if "tw_token" not in session:
        flash("❌ Please login to Twitter first.", "error")
        return redirect(url_for("index", active_tab="twitter"))

    msg = request.form.get("message")
    new_post = {
        "id": str(len(mock_twitter_posts) + 1),
        "message": msg,
        "created_time": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "violations": check_compliance(msg),
    }
    mock_twitter_posts.insert(0, new_post)

    flash("✅ Twitter post published (demo)", "success")
    return redirect(url_for("tw_collect"))

@app.route("/twitter/delete", methods=["POST"])
def tw_delete():
    if "tw_token" not in session:
        flash("❌ Please log in with Twitter first.", "error")
        return redirect(url_for("index", active_tab="twitter"))

    post_id = request.form.get("post_id")
    global mock_twitter_posts
    mock_twitter_posts = [p for p in mock_twitter_posts if p["id"] != post_id]

    flash("🗑️ Twitter post deleted (demo)", "success")
    return redirect(url_for("tw_collect"))


# ---------------- Main ----------------
@app.route("/")
def index():
    return render_template("index.html", active_tab="facebook")

if __name__ == "__main__":
    print("🚀 App running at http://localhost:8000/")
    app.run(host="localhost", port=8000, debug=True)
