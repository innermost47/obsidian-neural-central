from server.config import settings


def base_template(
    content_html: str,
    preheader: str = "",
    unsubscribe_token: str = "",
    title: str = "OBSIDIAN Neural",
) -> str:
    unsubscribe_url = (
        f"{settings.APP_URL}/api/v1/auth/unsubscribe?token={unsubscribe_token}"
        if unsubscribe_token
        else f"{settings.APP_URL}/api/v1/auth/unsubscribe"
    )

    preheader_html = (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
        f"{preheader}&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌&nbsp;‌"
        f"</div>"
        if preheader
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta http-equiv="X-UA-Compatible" content="IE=edge"/>
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Courier+Prime&family=Inter:wght@400;600;700&display=swap" rel="stylesheet"/>
  <!--[if mso]>
  <noscript>
    <xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml>
  </noscript>
  <![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#ffffff;font-family:'Inter',Helvetica,Arial,sans-serif;color:#1a1a1a;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

{preheader_html}

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#ffffff;">
  <tr>
    <td align="center" style="padding:40px 20px;">

      <!-- Wrapper -->
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background-color:#fafafa;border:1px solid #cccccc;border-radius:16px;overflow:hidden;">

        <!-- Top bar -->
        <tr>
          <td style="background:linear-gradient(135deg,#b8605c 0%,#8b4545 100%);height:6px;font-size:0;line-height:0;">&nbsp;</td>
        </tr>

        <!-- Header -->
        <tr>
          <td style="padding:32px 40px 24px;border-bottom:1px solid #e8e8e8;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <span style="display:inline-block;background:#b8605c;color:#ffffff;font-family:'Courier Prime','Courier New',monospace;font-weight:700;font-size:13px;letter-spacing:2px;padding:6px 14px;border-radius:6px;">ON</span>
                  <span style="font-family:'Courier Prime','Courier New',monospace;font-size:11px;color:#cccccc;letter-spacing:3px;text-transform:uppercase;margin-left:12px;vertical-align:middle;">OBSIDIAN Neural</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Content -->
        <tr>
          <td style="padding:32px 40px;">
            {content_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:24px 40px;border-top:1px solid #e8e8e8;background-color:#f5f5f5;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font-size:12px;color:#cccccc;font-family:'Inter',Helvetica,Arial,sans-serif;line-height:1.8;">
                  <p style="margin:0 0 8px;">
                    <a href="{settings.FRONTEND_URL}" style="color:#b8605c;text-decoration:none;font-weight:600;">{settings.FRONTEND_URL}</a>
                    &nbsp;&bull;&nbsp;
                    <a href="{settings.REPO_URL} style="color:#cccccc;text-decoration:none;">GitHub</a>
                    &nbsp;&bull;&nbsp;
                    <a href="{settings.FRONTEND_URL}/dashboard.html" style="color:#cccccc;text-decoration:none;">Dashboard</a>
                  </p>
                  <p style="margin:0 0 8px;color:#cccccc;">
                    Presented at AES AIMLA 2025 &mdash; Queen Mary University London
                  </p>
                  <p style="margin:0;">
                    <a href="{unsubscribe_url}" style="color:#cccccc;text-decoration:underline;font-size:11px;">Unsubscribe from marketing emails</a>
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>
      <!-- /Wrapper -->

    </td>
  </tr>
</table>

</body>
</html>"""


def btn_primary(text: str, url: str) -> str:
    return f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr>
    <td align="center" style="padding:8px 0;">
      <table cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center" bgcolor="#b8605c" style="border-radius:8px;">
            <!--[if mso]>
            <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml"
              href="{url}"
              style="height:50px;v-text-anchor:middle;width:280px;"
              arcsize="10%" fillcolor="#b8605c" strokecolor="#b8605c">
              <v:fill type="solid" color="#b8605c"/>
              <w:anchorlock/>
              <center style="color:white;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">{text}</center>
            </v:roundrect>
            <![endif]-->
            <!--[if !mso]><!-->
            <a href="{url}"
               style="display:block;padding:14px 32px;color:#ffffff;text-decoration:none;font-family:'Inter',Helvetica,Arial,sans-serif;font-weight:700;font-size:16px;text-align:center;border-radius:8px;background:#b8605c;mso-hide:all;white-space:nowrap;">
              {text}
            </a>
            <!--<![endif]-->
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def btn_secondary(text: str, url: str) -> str:
    return f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr>
    <td align="center" style="padding:8px 0;">
      <table cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center" style="border-radius:8px;border:1px solid #cccccc;">
            <a href="{url}"
               style="display:block;padding:12px 32px;color:#4a4a4a;text-decoration:none;font-family:'Inter',Helvetica,Arial,sans-serif;font-size:14px;text-align:center;border-radius:8px;white-space:nowrap;">
              {text}
            </a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def info_box(content_html: str) -> str:
    return f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:20px 0;">
  <tr>
    <td style="background-color:#f5f5f5;border:1px solid #e0e0e0;border-left:4px solid #b8605c;border-radius:8px;padding:20px;">
      {content_html}
    </td>
  </tr>
</table>"""


def stat_row(label: str, value: str) -> str:
    return f"""
<tr>
  <td style="padding:8px 0;color:#4a4a4a;font-family:'Courier Prime','Courier New',monospace;font-size:12px;width:160px;vertical-align:top;text-transform:uppercase;letter-spacing:1px;">{label}</td>
  <td style="padding:8px 0;color:#1a1a1a;font-size:14px;font-weight:600;">{value}</td>
</tr>"""


def section_title(text: str) -> str:
    return f"""<h3 style="color:#b8605c;font-family:'Courier Prime','Courier New',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin:0 0 12px;border-bottom:1px solid #e8e8e8;padding-bottom:8px;">{text}</h3>"""


def download_buttons(repo_url: str) -> str:
    links = {
        "Windows VST3": f"{repo_url}/releases/latest/download/OBSIDIAN-Neural-Windows-VST3.zip",
        "macOS VST3": f"{repo_url}/releases/latest/download/OBSIDIAN-Neural-macOS-VST3.zip",
        "macOS AU": f"{repo_url}/releases/latest/download/OBSIDIAN-Neural-macOS-AU.zip",
        "Linux VST3": f"{repo_url}/releases/latest/download/OBSIDIAN-Neural-Linux-VST3.tar.gz",
    }
    cells = ""
    for i, (label, url) in enumerate(links.items()):
        bg = "#b8605c" if i < 2 else "#4a4a4a"
        cells += f"""
    <td width="50%" style="padding:4px;">
      <a href="{url}" style="display:block;background:{bg};color:#ffffff;text-decoration:none;padding:10px;border-radius:6px;text-align:center;font-size:13px;font-weight:600;font-family:'Inter',Helvetica,Arial,sans-serif;">{label}</a>
    </td>"""
        if i % 2 == 1:
            cells += "</tr><tr>"

    return f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:16px 0;">
  <tr>{cells}</tr>
</table>"""
