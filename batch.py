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

# ---------------- Instagram (mock) ----------------
ig_posts_by_account = {}  # {account_id: [ {id, message, created_time, violations} ]}


app = Flask(__name__)
app.secret_key = "supersecretkey"  # session + flash

@app.route('/intro')
def intro():
    return render_template('intro.html')

@app.route('/delete')
def delete():
    return render_template('delete.html')

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

@app.route("/oauth/instagram/login")
def ig_login():
    ig_oauth_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth"
        f"?client_id={FACEBOOK_APP_ID}"
        f"&redirect_uri={IG_REDIRECT_URI}"
        f"&scope={IG_SCOPES}"
    )
    return redirect(ig_oauth_url)

@app.route("/oauth/instagram/callback")
def ig_callback():
    code = request.args.get("code")
    if not code:
        flash("❌ No code returned from Instagram/Facebook", "error")
        return redirect(url_for("index", active_tab="instagram"))

    # Exchange short-lived
    token_url = f"{GRAPH_URL}/oauth/access_token"
    params = {
        "client_id": FACEBOOK_APP_ID,
        "redirect_uri": IG_REDIRECT_URI,
        "client_secret": FACEBOOK_APP_SECRET,
        "code": code,
    }
    res = requests.get(token_url, params=params).json()
    access_token = res.get("access_token")
    if not access_token:
        flash(f"❌ Failed to get IG token: {res}", "error")
        return redirect(url_for("index", active_tab="instagram"))

    # Exchange to long-lived
    res2 = requests.get(
        f"{GRAPH_URL}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": FACEBOOK_APP_ID,
            "client_secret": FACEBOOK_APP_SECRET,
            "fb_exchange_token": access_token,
        }
    ).json()
    session["ig_token"] = res2.get("access_token", access_token)
    flash("✅ Instagram connected", "success")
    return redirect(url_for("index", active_tab="instagram"))

@app.route("/instagram/collect", methods=["POST"])
def ig_collect():
    token = session.get("ig_token")
    if not token:
        flash("❌ Please log in with Instagram first.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    # Use provided account_id if numeric; otherwise fall back to the linked IG account
    raw = (request.form.get("account_id") or "").strip()
    ig_user_id = raw if raw.isdigit() else get_primary_ig_user_id(token)
    if not ig_user_id:
        flash("❌ No linked Instagram Business/Creator account found.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    session["ig_last_account"] = ig_user_id
    posts = ig_fetch_posts(ig_user_id, token)
    return render_template("index.html", ig_all_posts={ig_user_id: posts}, active_tab="instagram")

@app.route("/instagram/create", methods=["POST"])
def ig_create():
    token = session.get("ig_token")
    if not token:
        flash("❌ Please log in with Instagram first.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    ig_user_id = (request.form.get("account_id") or session.get("ig_last_account") or "").strip()
    if not ig_user_id:
        ig_user_id = get_primary_ig_user_id(token)
    if not ig_user_id:
        flash("❌ Missing IG account.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    caption = request.form.get("message", "")
    image_url = (request.form.get("image_url") or "").strip()
    if not image_url:
        flash("❌ IG API requires an image_url to publish.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    # Step 1: create container
    container = requests.post(
        f"{GRAPH_URL}/{ig_user_id}/media",
        params={"image_url": image_url, "caption": caption, "access_token": token}
    ).json()

    if "id" not in container:
        flash(f"❌ Failed to create media: {container}", "error")
        return redirect(url_for("index", active_tab="instagram"))

    # Step 2: publish
    publish = requests.post(
        f"{GRAPH_URL}/{ig_user_id}/media_publish",
        params={"creation_id": container["id"], "access_token": token}
    ).json()

    if "id" in publish:
        v = check_compliance(caption)
        if v:
            flash(f"⚠️ Compliance issues detected: {', '.join(v)}", "error")
        flash("✅ Instagram post published.", "success")
    else:
        flash(f"❌ Publish failed: {publish}", "error")

    # Re-run collect to refresh list
    return redirect(url_for("ig_collect"), code=307)

@app.route("/instagram/delete", methods=["POST"])
def ig_delete():
    token = session.get("ig_token")
    if not token:
        flash("❌ Please log in with Instagram first.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    post_id = request.form.get("post_id", "")
    if not post_id:
        flash("❌ Missing post_id.", "error")
        return redirect(url_for("index", active_tab="instagram"))

    res = requests.delete(f"{GRAPH_URL}/{post_id}", params={"access_token": token}).json()
    if res.get("success"):
        flash("🗑️ Instagram post deleted.", "success")
    else:
        flash(f"❌ Delete failed: {res}", "error")

    return redirect(url_for("ig_collect"), code=307)

@app.route("/instagram/subscribe_about", methods=["POST"])
def ig_subscribe_about():
    flash("ℹ️ IG bio change webhooks aren’t available here.", "warning")
    return redirect(url_for("index", active_tab="instagram"))



@app.route("/debug/config")
def debug_config():
    return {
        "APP_ENV": APP_ENV,
        "FACEBOOK_REDIRECT_URI": FACEBOOK_REDIRECT_URI,
        "FACEBOOK_APP_ID": FACEBOOK_APP_ID,
        "Current Host": request.host_url
    }

@app.route("/deauthorize", methods=["GET", "POST"])
def deauthorize():
    if request.method == "GET":
        # Serve the deauthorize.html template
        return render_template("deauthorize.html")
    else:
        # Handle the POST request (actual deauthorization logic)
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user_id")

        # Log the deauthorization
        print(f"❌ User {user_id} deauthorized the app")

        # You could also remove their tokens/data here if you're storing any
        # For example:
        # - Clear any stored Facebook tokens
        # - Mark user as deauthorized in database
        # - Clean up any cached data
        
        return "ok", 200

@app.route("/debug/token")  
def debug_token():
    if "current_user" not in user_tokens:
        return {"status": "No token found", "connected": False}
    
    token = user_tokens["current_user"]
    test_url = f"{GRAPH_URL}/me"
    params = {"access_token": token}
    
    try:
        response = requests.get(test_url, params=params)
        return response.json()
    except Exception as e:
        return {"error": str(e)}
    
# ---------------- Config ----------------
APP_ENV = os.getenv("APP_ENV", "dev")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "change-me")

if APP_ENV == "prod":
    FACEBOOK_REDIRECT_URI = os.getenv(
        "FACEBOOK_REDIRECT_URI",
        "https://www.advisorcheck.onrender.com/oauth/facebook/callback"
    )
else:
    FACEBOOK_REDIRECT_URI = os.getenv(
        "FACEBOOK_REDIRECT_URI",
        "https://www.advisorcheck.info/oauth/facebook/callback"
    )

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "1718598702202188")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "ba989ffd85f6244abb11e80f4bcd5064")

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "7812hhk04l7tik")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "WPL_AP1.EzmF4sKA4W89UiIT.4TGhmg==")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/oauth/linkedin/callback")

TWITTER_CLIENT_ID = os.getenv("TWITTER_CLIENT_ID", "N2NkMEkwMmFtaVBCME5Iem05cWs6MTpjaQ")
TWITTER_CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET", "FsEgafSt737aJwbCco3QJk3vqXq9lpq19LNwkgcJGes8yZqET3")
TWITTER_REDIRECT_URI = os.getenv("TWITTER_REDIRECT_URI", "http://localhost:8000/oauth/twitter/callback")

GRAPH_URL = "https://graph.facebook.com/v18.0"

# ---------------- Instagram Config ----------------
# Uses same Facebook App ID/Secret; separate redirect just for IG callback
if APP_ENV == "prod":
    IG_REDIRECT_URI = os.getenv("IG_REDIRECT_URI", "https://www.advisorcheck.onrender.com/oauth/instagram/callback")
else:
    IG_REDIRECT_URI = os.getenv("IG_REDIRECT_URI", "http://localhost:8000/oauth/instagram/callback")

IG_SCOPES = "instagram_basic,instagram_manage_comments,instagram_manage_insights,instagram_content_publish,pages_show_list,pages_read_engagement"


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

def get_primary_ig_user_id(user_token):
    """Return the IG Business/Creator user id linked to one of the user's Pages."""
    url = f"{GRAPH_URL}/me/accounts"
    params = {"access_token": user_token, "fields": "name,access_token,instagram_business_account"}
    res = requests.get(url, params=params).json()
    for page in res.get("data", []):
        ig = page.get("instagram_business_account")
        if ig and ig.get("id"):
            return ig["id"]
    return None

def ig_fetch_posts(ig_user_id, token):
    posts = []
    url = f"{GRAPH_URL}/{ig_user_id}/media"
    params = {"access_token": token, "fields": "id,caption,timestamp,permalink,media_type"}
    try:
        data = requests.get(url, params=params).json()
        for m in data.get("data", []):
            msg = m.get("caption", "[No caption]")
            posts.append({
                "id": m.get("id"),
                "message": msg,
                "created_time": format_date(m.get("timestamp", "")),
                "platform": "instagram",
                "violations": check_compliance(msg)
            })
    except Exception as e:
        print(f"❌ IG fetch error: {e}")
    return posts


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
# Keep last-seen About for quick display/logging
last_about = {}  # { page_id: {"about": "...", "fetched_at": "ISO"} }

def get_page_token_for(page_id, user_token):
    """Find a Page token for page_id using the current user token (if not in cache)."""
    # 1) Try in-memory map first (you already fill current_pages on collect)
    if page_id in current_pages:
        return current_pages[page_id]

    # 2) Fallback: look up via /me/accounts
    url = f"{GRAPH_URL}/me/accounts"
    res = requests.get(url, params={"access_token": user_token}).json()
    for p in res.get("data", []):
        if p.get("id") == page_id:
            return p.get("access_token")
    return None

def fb_fetch_about_fields(page_id, page_token):
    """Read only the Page's About/General Info style fields."""
    fields = "about,general_info,description,company_overview,mission"
    url = f"{GRAPH_URL}/{page_id}"
    res = requests.get(url, params={"access_token": page_token, "fields": fields}).json()
    # Prefer about/general_info; fall back to description if present
    about_text = res.get("about") or res.get("general_info") or res.get("description") or ""
    last_about[page_id] = {"about": about_text, "fetched_at": datetime.utcnow().isoformat()+"Z"}
    print(f"ℹ️ [About updated] Page {page_id}: {about_text[:200]}")
    return res


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
    print(f"🔑 Facebook callback received. Code: {code[:20] if code else 'None'}...")
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

    print(f"📞 Requesting token from: {token_url}")
    print(f"🔧 With redirect_uri: {FACEBOOK_REDIRECT_URI}")

    res = requests.get(token_url, params=params).json()
    access_token = res.get("access_token")
    print(f"🎯 Token response: {res}")
    print(f"🎟️ Final token stored: {user_tokens.get('current_user', 'None')[:20]}...")

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
    print(f"🔄 Exchanging for long-lived token...")

    res2 = requests.get(long_token_url, params=params).json()
    print(f"🎯 Long token response: {res2}")

    long_token = res2.get("access_token", access_token)
    print(f"🎟️ Long token: {long_token[:20] if long_token else 'None'}...")

    user_tokens["current_user"] = long_token
    print(f"💾 Stored token. Current tokens: {list(user_tokens.keys())}")

    flash("✅ Facebook login successful", "success")
    return redirect(url_for("index"))

def resolve_page_id(page_input, token):
    """Convert page name/URL to numeric page ID"""
    cleaned = page_input.strip()
    
    # Extract page name from URL
    if 'facebook.com' in cleaned:
        match = re.search(r'facebook\.com/([^/?#&]+)', cleaned)
        if match:
            cleaned = match.group(1)
    
    # If it's already numeric, return as-is
    if cleaned.isdigit():
        return cleaned
    
    # Convert vanity name to page ID
    url = f"{GRAPH_URL}/{cleaned}"
    params = {'fields': 'id,name', 'access_token': token}
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if 'id' in data:
            print(f"✅ Resolved '{page_input}' to page ID: {data['id']} ({data.get('name', 'Unknown')})")
            return data['id']
        else:
            print(f"❌ Could not resolve page: {data}")
            return cleaned
            
    except Exception as e:
        print(f"❌ Error resolving page ID: {e}")
        return cleaned

def fb_fetch_public_page_posts(page_id, token):
    """Fetch public posts from any Facebook Page"""
    posts = []
    
    url = f"{GRAPH_URL}/{page_id}/posts"
    params = {
        'access_token': token,
        'fields': 'id,message,story,created_time,from,type,status_type',
        'limit': 25
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if 'error' in data:
            print(f"❌ Error fetching posts for {page_id}: {data['error']}")
            return posts
            
        for post in data.get('data', []):
            message = post.get('message', post.get('story', '[No text content]'))
            violations = check_compliance(message)
            
            posts.append({
                'id': post.get('id'),
                'message': message,
                'created_time': format_date(post.get('created_time', '')),
                'platform': 'facebook',
                'violations': violations,
                'post_type': post.get('type', 'unknown'),
                'from': post.get('from', {}).get('name', 'Unknown')
            })
        
        print(f"✅ Fetched {len(posts)} posts from page {page_id}")
            
    except Exception as e:
        print(f"❌ Exception fetching posts from {page_id}: {e}")
    
    return posts

def resolve_page_id_and_token(user_input, user_token):
    """
    Resolve a user-supplied Page ID/URL to (page_id, page_token).
    Always prefer a Page token from /me/accounts if available.
    """
    cleaned = user_input.strip()

    # Handle full URLs (facebook.com/PageName)
    if "facebook.com" in cleaned:
        match = re.search(r"facebook\.com/([^/?#]+)", cleaned)
        if match:
            cleaned = match.group(1).strip("/")

    # Convert vanity name -> numeric ID
    if not cleaned.isdigit():
        url = f"{GRAPH_URL}/{cleaned}"
        params = {"fields": "id,name", "access_token": user_token}
        res = requests.get(url, params=params).json()
        print("👉 Vanity resolution:", res)
        if "id" in res:
            cleaned = res["id"]

    # Try to get Page tokens for Pages the user manages
    url = f"{GRAPH_URL}/me/accounts"
    params = {"access_token": user_token}
    res = requests.get(url, params=params).json()
    print("👉 Managed Pages:", res)

    for page in res.get("data", []):
        if page["id"] == cleaned:
            print(f"✅ Found page token for {page['name']} ({page['id']})")
            return page["id"], page["access_token"]

    # Fallback: return user token (limited unless PPCA approved)
    print("⚠️ No page token found — falling back to user token")
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
    raw_input = request.form.get("page_id", "").strip()
    
    if not raw_input:
        flash("❌ Please enter a Facebook Page ID, username, or URL.", "error")
        return redirect(url_for("index"))
    
    # Split multiple pages by comma
    page_inputs = [p.strip() for p in raw_input.split(",") if p.strip()]
    all_posts = {}
    
    for page_input in page_inputs:
        try:
            # First, resolve the page input to a numeric page ID
            page_id = resolve_page_id(page_input, user_token)
            
            # Try to get managed page token first (for pages you admin)
            managed_page_id, page_token = resolve_page_id_and_token(page_input, user_token)
            
            # If we got a page token (meaning you manage this page), use it
            if page_token != user_token:
                print(f"📋 Using page token for managed page: {page_id}")
                posts = fb_fetch_posts(managed_page_id, page_token)  # Your existing function
            else:
                print(f"🌐 Using public access for page: {page_id}")
                posts = fb_fetch_public_page_posts(page_id, user_token)  # New function
            
            if posts:
                all_posts[page_id] = posts
                current_pages[page_id] = page_token  # Store for create/delete operations
                flash(f"✅ Fetched {len(posts)} posts from {page_input}", "success")
            else:
                flash(f"⚠️ No posts found for {page_input}. This could mean: "
                      f"1) The page has no public posts, 2) The page doesn't exist, "
                      f"or 3) The page restricts access.", "warning")
                
        except Exception as e:
            flash(f"❌ Error fetching posts for {page_input}: {str(e)}", "error")
            print(f"❌ Full error for {page_input}: {e}")
    
    if page_inputs:
        session["fb_last_page"] = raw_input
    
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

@app.route("/webhooks/facebook", methods=["GET", "POST"])
def fb_webhook():
    if request.method == "GET":
        # Verification handshake
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403

    # POST: handle change notifications
    data = request.get_json(force=True, silent=True) or {}
    entries = data.get("entry", [])
    for entry in entries:
        page_id = str(entry.get("id"))
        for change in entry.get("changes", []):
            if change.get("field") == "general_info":
                # 🔔 Only react to About/General Info changes
                user_token = user_tokens.get("current_user")  # you already set this
                page_token = get_page_token_for(page_id, user_token) if user_token else None
                if page_token:
                    fb_fetch_about_fields(page_id, page_token)
                else:
                    print(f"⚠️ No page token available to fetch About for page {page_id}")
    return "ok", 200

@app.route("/facebook/subscribe_about", methods=["POST"])
def fb_subscribe_about():
    if "current_user" not in user_tokens:
        flash("❌ Please log in with Facebook first.", "error")
        return redirect(url_for("index"))

    user_token = user_tokens["current_user"]
    raw_input = request.form.get("page_id", "").strip()
    if not raw_input:
        flash("❌ Enter a Page ID/URL first.", "error")
        return redirect(url_for("index"))

    # Reuse your helper to get numeric ID + page token
    page_id, page_token = resolve_page_id_and_token(raw_input, user_token)
    if not page_token or page_token == user_token:
        flash("⚠️ Could not get a Page token (are you an admin of this Page?).", "warning")
        return redirect(url_for("index"))

    # Subscribe only to 'general_info' — the About field webhook
    url = f"{GRAPH_URL}/{page_id}/subscribed_apps"
    params = {"access_token": page_token, "subscribed_fields": "general_info"}
    res = requests.post(url, params=params).json()
    if res.get("success"):
        flash("✅ Subscribed to About updates for this Page.", "success")
    else:
        flash(f"❌ Subscription failed: {res}", "error")
    return redirect(url_for("index"))


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

@app.route("/")
def index():
    # if you have templates/index.html
    return render_template("index.html", active_tab=request.args.get("active_tab", "facebook"))
    # or, if you prefer to land on /intro:
    # return redirect(url_for("intro"))


if __name__ == "__main__":
    print("🚀 App running at http://localhost:8000/")
    app.run(host="localhost", port=8000, debug=True)
