import datetime
from flask import Blueprint, render_template, request
from CTFd.models import db, Solves, Awards, Challenges, Users, Teams
from CTFd.utils import get_config

# --- CONFIGURATION ---
FIRST_BLOOD_BONUS = 20
# ---------------------


# 1. Database Model to Track First Blood Holders
class FirstBlood(db.Model):
    __tablename__ = "first_bloods"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"))


def sync_all_first_bloods():
    """Recalculates First Bloods to handle bans, hidden users, and deleted solves."""
    user_mode = get_config("user_mode")
    challenges = Challenges.query.all()

    for chal in challenges:
        # Find the oldest VALID solve (ignoring banned/hidden users)
        query = Solves.query.join(Users, Solves.user_id == Users.id).filter(
            Solves.challenge_id == chal.id, Users.banned == False, Users.hidden == False
        )

        if user_mode == "teams":
            query = query.join(Teams, Solves.team_id == Teams.id).filter(
                Teams.banned == False, Teams.hidden == False
            )

        first_solve = query.order_by(Solves.date.asc(), Solves.id.asc()).first()
        tracker = FirstBlood.query.filter_by(challenge_id=chal.id).first()

        if not first_solve:
            # No valid solves exist. If a tracker/award exists, clean it up.
            if tracker:
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
                db.session.commit()
            continue

        if tracker:
            # If the current tracker matches the valid first solve, move to the next challenge
            if (
                tracker.user_id == first_solve.user_id
                and tracker.team_id == first_solve.team_id
            ):
                continue
            else:
                # Mismatch! The previous first blood was banned/deleted. Delete old award.
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
                db.session.commit()

        # Issue the new First Blood Award to the correct solver
        award = Awards(
            user_id=first_solve.user_id,
            team_id=first_solve.team_id,
            name="First Blood Bonus",
            description=f"First Blood: {chal.name}",
            value=FIRST_BLOOD_BONUS,
            category="first_blood",
            icon="shield",
        )
        db.session.add(award)
        db.session.commit()  # Commit to get the award's ID

        # Track it so we don't duplicate it
        new_tracker = FirstBlood(
            challenge_id=chal.id,
            user_id=first_solve.user_id,
            team_id=first_solve.team_id,
            award_id=award.id,
        )
        db.session.add(new_tracker)
        db.session.commit()


def load(app):
    # Initialize our custom database table
    app.db.create_all()

    # 2. Blueprint for the public First Bloods Page
    first_blood_bp = Blueprint("first_bloods", __name__, template_folder="templates")

    @first_blood_bp.route("/first-bloods", methods=["GET"])
    def first_bloods_page():
        # Query our tracker table directly, as it is always perfectly in sync
        bloods_data = FirstBlood.query.all()
        bloods = []

        for b in bloods_data:
            chal = Challenges.query.get(b.challenge_id)
            if not chal or chal.state != "visible":
                continue

            user = Users.query.get(b.user_id)
            team = Teams.query.get(b.team_id) if b.team_id else None
            solve = Solves.query.filter_by(
                challenge_id=b.challenge_id, user_id=b.user_id
            ).first()

            bloods.append(
                {
                    "challenge_name": chal.name,
                    "user_name": user.name if user else "Unknown",
                    "team_name": team.name if team else "None",
                    "date": solve.date if solve else None,
                }
            )

        # Sort by date, most recent at the top
        bloods.sort(
            key=lambda x: x["date"] if x["date"] else datetime.datetime.min,
            reverse=True,
        )
        return render_template("first_bloods.html", bloods=bloods)

    app.register_blueprint(first_blood_bp)

    # 3. Dynamic Background Engine
    @app.after_request
    def trigger_first_blood_sync(response):
        """
        Runs automatically after any request finishes. We only execute the sync
        if the request modified a solve, a user, or a team.
        """
        if request.method in ["POST", "PATCH", "DELETE"]:
            path = request.path
            if (
                path.startswith("/api/v1/challenges/attempt")
                or path.startswith("/api/v1/users")
                or path.startswith("/api/v1/teams")
                or path.startswith("/api/v1/solves")
            ):
                try:
                    sync_all_first_bloods()
                except Exception as e:
                    print(f"[First Blood Plugin] Sync Error: {e}")
        return response
