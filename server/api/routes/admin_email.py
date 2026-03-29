from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_
from server.core.database import get_db, User, EmailLog, EmailLogStatus
from server.api.dependencies import get_verified_user
from server.services.email_service import EmailService
from datetime import datetime, timedelta, timezone
from typing import Optional
from server.api.models import RetryEmailsRequest

router = APIRouter(prefix="/admin/emails", tags=["Admin - Emails"])


def get_admin_user(current_user: User = Depends(get_verified_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/logs", response_model=dict)
async def get_email_logs(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    email_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at|sent_at|status)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
):

    query = db.query(EmailLog)

    if status:
        try:
            status_enum = EmailLogStatus[status.upper()]
            query = query.filter(EmailLog.status == status_enum)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if email_type:
        query = query.filter(EmailLog.email_type == email_type)

    if search:
        query = query.filter(
            or_(
                EmailLog.recipient_email.ilike(f"%{search}%"),
                EmailLog.subject.ilike(f"%{search}%"),
            )
        )

    sort_column = getattr(EmailLog, sort_by)
    if sort_order == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)

    total = query.count()
    offset = (page - 1) * limit
    emails = query.offset(offset).limit(limit).all()

    email_list = [
        {
            "id": email.id,
            "recipient_email": email.recipient_email,
            "subject": email.subject,
            "email_type": email.email_type,
            "status": email.status.value,
            "error_message": email.error_message,
            "retry_count": email.retry_count,
            "user_id": email.user_id,
            "created_at": email.created_at.isoformat(),
            "sent_at": email.sent_at.isoformat() if email.sent_at else None,
            "last_retry_at": (
                email.last_retry_at.isoformat() if email.last_retry_at else None
            ),
        }
        for email in emails
    ]

    return {
        "emails": email_list,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/stats")
async def get_email_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
    days: int = Query(30, ge=1, le=365),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    total_emails = db.query(EmailLog).filter(EmailLog.created_at >= since).count()

    sent = (
        db.query(EmailLog)
        .filter(EmailLog.created_at >= since, EmailLog.status == EmailLogStatus.SENT)
        .count()
    )

    failed = (
        db.query(EmailLog)
        .filter(EmailLog.created_at >= since, EmailLog.status == EmailLogStatus.FAILED)
        .count()
    )

    pending = (
        db.query(EmailLog)
        .filter(EmailLog.created_at >= since, EmailLog.status == EmailLogStatus.PENDING)
        .count()
    )

    success_rate = (sent / total_emails * 100) if total_emails > 0 else 0

    by_type = {}
    type_stats = (
        db.query(EmailLog.email_type, func.count(EmailLog.id))
        .filter(EmailLog.created_at >= since)
        .group_by(EmailLog.email_type)
        .all()
    )

    for email_type, count in type_stats:
        by_type[email_type] = count

    return {
        "total_emails": total_emails,
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "success_rate": round(success_rate, 2),
        "by_type": by_type,
        "period_days": days,
    }


@router.get("/failed")
async def get_failed_emails(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
    limit: int = Query(100, ge=1, le=500),
):

    failed_emails = (
        db.query(EmailLog)
        .filter(EmailLog.status == EmailLogStatus.FAILED)
        .order_by(desc(EmailLog.created_at))
        .limit(limit)
        .all()
    )

    email_list = [
        {
            "id": email.id,
            "recipient_email": email.recipient_email,
            "subject": email.subject,
            "email_type": email.email_type,
            "error_message": email.error_message,
            "retry_count": email.retry_count,
            "created_at": email.created_at.isoformat(),
            "last_retry_at": (
                email.last_retry_at.isoformat() if email.last_retry_at else None
            ),
        }
        for email in failed_emails
    ]

    return {"failed_emails": email_list, "total": len(email_list)}


@router.post("/retry")
async def retry_failed_emails(
    request: RetryEmailsRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    if not request.email_ids:
        raise HTTPException(status_code=400, detail="No email IDs provided")

    failed_emails = db.query(EmailLog).filter(EmailLog.id.in_(request.email_ids)).all()

    if not failed_emails:
        raise HTTPException(status_code=404, detail="No emails found with given IDs")

    results = {"success": 0, "failed": 0, "details": []}

    for email_log in failed_emails:
        email_log.status = EmailLogStatus.RETRYING
        email_log.retry_count += 1
        email_log.last_retry_at = datetime.now(timezone.utc)
        db.commit()

        try:
            success = EmailService._send_email(
                email_log.recipient_email, email_log.subject, email_log.body
            )

            if success:
                email_log.status = EmailLogStatus.SENT
                email_log.sent_at = datetime.now(timezone.utc)
                email_log.error_message = None
                results["success"] += 1
                results["details"].append(
                    {
                        "id": email_log.id,
                        "email": email_log.recipient_email,
                        "status": "sent",
                    }
                )
            else:
                email_log.status = EmailLogStatus.FAILED
                email_log.error_message = "Retry failed - SMTP error"
                results["failed"] += 1
                results["details"].append(
                    {
                        "id": email_log.id,
                        "email": email_log.recipient_email,
                        "status": "failed",
                        "error": "SMTP error",
                    }
                )

            db.commit()

        except Exception as e:
            email_log.status = EmailLogStatus.FAILED
            email_log.error_message = f"Retry exception: {str(e)}"
            db.commit()
            results["failed"] += 1
            results["details"].append(
                {
                    "id": email_log.id,
                    "email": email_log.recipient_email,
                    "status": "failed",
                    "error": str(e),
                }
            )

    return {
        "message": f"Retry completed: {results['success']} sent, {results['failed']} failed",
        "results": results,
    }


@router.get("/detail/{email_id}")
async def get_email_detail(
    email_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):

    email_log: EmailLog = db.query(EmailLog).filter(EmailLog.id == email_id).first()

    if not email_log:
        raise HTTPException(status_code=404, detail="Email log not found")

    user_info = None
    if email_log.user_id:
        user = db.query(User).filter(User.id == email_log.user_id).first()
        if user:
            user_info = {
                "id": user.id,
                "email": user.email,
                "subscription_tier": user.subscription_tier,
            }

    return {
        "id": email_log.id,
        "recipient_email": email_log.recipient_email,
        "subject": email_log.subject,
        "body": email_log.body,
        "email_type": email_log.email_type,
        "status": email_log.status.value,
        "error_message": email_log.error_message,
        "retry_count": email_log.retry_count,
        "user_id": email_log.user_id,
        "user_info": user_info,
        "created_at": email_log.created_at.isoformat(),
        "sent_at": email_log.sent_at.isoformat() if email_log.sent_at else None,
        "last_retry_at": (
            email_log.last_retry_at.isoformat() if email_log.last_retry_at else None
        ),
    }


@router.delete("/logs/{email_id}")
async def delete_email_log(
    email_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    email_log = db.query(EmailLog).filter(EmailLog.id == email_id).first()

    if not email_log:
        raise HTTPException(status_code=404, detail="Email log not found")

    db.delete(email_log)
    db.commit()

    return {"message": f"Email log {email_id} deleted successfully"}


@router.delete("/logs/cleanup")
async def cleanup_old_logs(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
    days: int = Query(90, ge=30, le=365),
    status: Optional[str] = Query(None),
):
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    query = db.query(EmailLog).filter(EmailLog.created_at < cutoff_date)

    if status:
        try:
            status_enum = EmailLogStatus[status.upper()]
            query = query.filter(EmailLog.status == status_enum)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    count = query.count()
    query.delete(synchronize_session=False)
    db.commit()

    return {
        "message": f"Deleted {count} email logs older than {days} days",
        "deleted_count": count,
    }
