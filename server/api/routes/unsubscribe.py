from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from server.config import settings
from server.core.database import get_db, User

router = APIRouter()


@router.get("/auth/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.unsubscribe_token == token).first()

    if not user:
        return HTMLResponse(
            content=_page(
                title="Invalid link",
                emoji="🔒",
                heading="Invalid or expired link.",
                body="This unsubscribe link is no longer valid.",
                color="#cccccc",
            ),
            status_code=404,
        )

    if not user.accept_news_updates:
        return HTMLResponse(
            content=_page(
                title="Already unsubscribed",
                emoji="✅",
                heading="You're already unsubscribed.",
                body="You won't receive any more marketing emails from OBSIDIAN Neural.",
                color="#b8605c",
            )
        )

    user.accept_news_updates = False
    db.commit()

    return HTMLResponse(
        content=_page(
            title="Unsubscribed",
            emoji="👋",
            heading="You've been unsubscribed.",
            body="You won't receive any more marketing emails. Transactional emails (password reset, billing) will still be sent.",
            color="#b8605c",
        )
    )


def _page(title: str, emoji: str, heading: str, body: str, color: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{title} — OBSIDIAN Neural</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet"/>
  <style>
    body {{ margin:0;padding:0;background:#ffffff;font-family:'Inter',Helvetica,Arial,sans-serif;color:#1a1a1a;display:flex;align-items:center;justify-content:center;min-height:100vh; }}
    .card {{ background:#fafafa;border:1px solid #cccccc;border-radius:16px;padding:48px 40px;max-width:480px;width:90%;text-align:center; }}
    .bar {{ height:6px;background:linear-gradient(135deg,#b8605c,#8b4545);border-radius:8px 8px 0 0;margin:-48px -40px 40px; }}
    .emoji {{ font-size:3rem;margin-bottom:16px;display:block; }}
    h1 {{ font-size:22px;font-weight:700;margin:0 0 12px;color:#1a1a1a; }}
    p {{ color:#4a4a4a;font-size:14px;line-height:1.7;margin:0 0 24px; }}
    a {{ color:{color};text-decoration:none;font-weight:600; }}
    a:hover {{ text-decoration:underline; }}
    .logo {{ font-family:monospace;font-size:11px;color:#cccccc;letter-spacing:3px;text-transform:uppercase;margin-top:24px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="bar"></div>
    <span class="emoji">{emoji}</span>
    <h1>{heading}</h1>
    <p>{body}</p>
    <a href="{settings.FRONTEND_URL}">{settings.FRONTEND_URL}</a>
    <p class="logo">OBSIDIAN Neural</p>
  </div>
</body>
</html>"""
