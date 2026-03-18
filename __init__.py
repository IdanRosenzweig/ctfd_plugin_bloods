import datetime
from flask import Blueprint, render_template, request
from CTFd.models import db, Solves, Awards, Challenges, Users, Teams
from CTFd.utils import get_config
from CTFd.plugins import register_user_page_menu_bar

# --- CONFIGURATION ---
# Set the points for 1st, 2nd, and 3rd place
BLOOD_BONUSES = {
    1: 20,
    2: 10,
    3: 5
}
# ---------------------

# 1. Database Model to Track First/Second/Third Blood Holders
class BloodAward(db.Model):
    __tablename__ = "blood_awards"  # Changed table name to force a fresh schema creation
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"))
    position = db.Column(db.Integer) # Will store 1, 2, or 3


def sync_all_bloods():
    """Recalculates Bloods to handle bans, hidden users, and deleted solves."""
    user_mode = get_config("user_mode")
    challenges = Challenges.query.all()

    for chal in challenges:
        # Find the oldest VALID solves (ignoring banned/hidden users)
        query = Solves.query.join(Users, Solves.user_id == Users.id).filter(
            Solves.challenge_id == chal.id, Users.banned == False, Users.hidden == False
        )

        if user_mode == "teams":
            query = query.join(Teams, Solves.team_id == Teams.id).filter(
                Teams.banned == False, Teams.hidden == False
            )

        # Get the top 3 solves
        top_solves = query.order_by(Solves.date.asc(), Solves.id.asc()).limit(3).all()
        
        # Mapping of expected position (1, 2, 3) -> solve object
        valid_state = {i + 1: solve for i, solve in enumerate(top_solves)}
        
        # Get all current blood awards for this challenge
        trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
        
        trackers_to_keep = []

        # 1. Evaluate existing awards. Delete any that no longer match the true top 3.
        for tracker in trackers:
            expected_solve = valid_state.get(tracker.position)
            
            # If the user/team for this position still matches, keep it.
            if expected_solve and tracker.user_id == expected_solve.user_id and tracker.team_id == expected_solve.team_id:
                trackers_to_keep.append(tracker.position)
            else:
                # Mismatch! (e.g., a solver was deleted or bumped up a rank). Delete old award.
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
                
        db.session.commit()

        # 2. Issue new awards for positions that are missing
        for pos, solve in valid_state.items():
            if pos not in trackers_to_keep:
                
                titles = {1: "First Blood", 2: "Second Blood", 3: "Third Blood"}
                icons = {1: "shield", 2: "crosshairs", 3: "star"}
                
                # Issue the Award to the user's profile
                award = Awards(
                    user_id=solve.user_id,
                    team_id=solve.team_id,
                    name=titles[pos],
                    description=f"{titles[pos]}: {chal.name}",
                    value=BLOOD_BONUSES[pos],
                    icon=icons[pos],
                )
                db.session.add(award)
                db.session.commit()  # Commit to get the award's ID

                # Track it in our plugin table
                new_tracker = BloodAward(
                    challenge_id=chal.id,
                    user_id=solve.user_id,
                    team_id=solve.team_id,
                    award_id=award.id,
                    position=pos
                )
                db.session.add(new_tracker)
                db.session.commit()


def load(app):
    app.db.create_all()
    
    register_user_page_menu_bar("First Bloods", "/first-bloods")

    first_blood_bp = Blueprint("first_bloods", __name__, template_folder="templates")

    @first_blood_bp.route("/first-bloods", methods=["GET"])
    def first_bloods_page():
        bloods_data = BloodAward.query.all()
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
                    "position": b.position # Added the rank so the template can display it
                }
            )

        # Sort by date descending (newest activity at the top)
        bloods.sort(
            key=lambda x: x["date"] if x["date"] else datetime.datetime.min,
            reverse=True,
        )
        return render_template("first_bloods.html", bloods=bloods)

    app.register_blueprint(first_blood_bp)

    @app.after_request
    def trigger_first_blood_sync(response):
        if request.method in ["POST", "PATCH", "DELETE"]:
            path = request.path
            if (
                path.startswith("/api/v1/challenges/attempt")
                or path.startswith("/api/v1/users")
                or path.startswith("/api/v1/teams")
                or path.startswith("/api/v1/solves")
            ):
                try:
                    sync_all_bloods()
                except Exception as e:
                    print(f"[First Blood Plugin] Sync Error: {e}")
        return response