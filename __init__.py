import datetime
from flask import Blueprint, render_template, request
from CTFd.models import db, Solves, Awards, Challenges, Users, Teams
from CTFd.utils import get_config
from CTFd.plugins import register_user_page_menu_bar

# --- CONFIGURATION ---
# Set the points for 1st, 2nd, and 3rd place
BLOODS_BONUSES = {
    1: 20,
    2: 10,
    3: 5
}

# Set the titles and icons for 1st, 2nd, and 3rd place
TITLES = {1: "First Blood", 2: "Second Blood", 3: "Third Blood"}
ICONS = {1: "crown", 2: "crown", 3: "crown"}
# ---------------------

class BloodAward(db.Model):
    __tablename__ = "blood_awards"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"))
    position = db.Column(db.Integer)


def sync_all_bloods():
    """Strictly enforces that the awards perfectly match the current top 3 solvers and configuration."""
    user_mode = get_config("user_mode")
    challenges = Challenges.query.all()

    for chal in challenges:
        # 1. Find the true, valid top 3 solves for this challenge
        query = Solves.query.join(Users, Solves.user_id == Users.id).filter(
            Solves.challenge_id == chal.id, Users.banned == False, Users.hidden == False
        )

        if user_mode == "teams":
            query = query.join(Teams, Solves.team_id == Teams.id).filter(
                Teams.banned == False, Teams.hidden == False
            )

        top_solves = query.order_by(Solves.date.asc(), Solves.id.asc()).limit(3).all()
        valid_state = {i + 1: solve for i, solve in enumerate(top_solves)}
        
        # 2. Grab all currently tracked awards for this challenge
        trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
        valid_positions_kept = []

        # 3. Aggressively clean up or update existing awards
        for tracker in trackers:
            expected_solve = valid_state.get(tracker.position)
            
            if expected_solve and tracker.user_id == expected_solve.user_id and tracker.team_id == expected_solve.team_id:
                # The solver is correct! Let's rigorously update the award details to match the config
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    award.name = TITLES[tracker.position]
                    award.description = f"{chal.name}"
                    award.value = BLOODS_BONUSES.get(tracker.position, 0)
                    award.icon = ICONS[tracker.position]
                    award.date = expected_solve.date
                    valid_positions_kept.append(tracker.position)
                else:
                    # The award was manually deleted by an admin, but the tracker remains. Delete tracker.
                    db.session.delete(tracker)
            else:
                # Mismatch! The solver was banned, deleted, or bumped rank. Delete the award and the tracker.
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
                
        db.session.commit()

        # 4. Issue missing awards for any position that isn't perfectly tracked
        for pos, solve in valid_state.items():
            if pos not in valid_positions_kept:
                # Create the physical award on the user's profile
                award = Awards(
                    user_id=solve.user_id,
                    team_id=solve.team_id,
                    name=TITLES[pos],
                    description=f"{chal.name}",
                    value=BLOODS_BONUSES.get(pos, 0),
                    icon=ICONS[pos],
                )
                db.session.add(award)
                db.session.commit()  # Commit to get the award's ID

                # Track it so we know exactly who has it
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
    
    # un an initial sync right when the server starts
    with app.app_context():
        try:
            sync_all_bloods()
            print("[Bloods Plugin] Initial sync completed successfully on startup!")
        except Exception as e:
            print(f"[Bloods Plugin] Initial sync failed during startup: {e}")
    
    # the bloods page
    bloods_bp = Blueprint("bloods", __name__, template_folder="templates")

    @bloods_bp.route("/bloods", methods=["GET"])
    def bloods_page():
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
                    "challenge_id": chal.id,
                    "user_name": user.name if user else "Unknown",
                    "user_id": user.id if user else None,
                    "team_name": team.name if team else "None",
                    "team_id": team.id if team else None,
                    "date": solve.date if solve else None,
                    "position": b.position                    
                }
            )

        # Sort by date descending
        bloods.sort(
            key=lambda x: x["date"] if x["date"] else datetime.datetime.min,
            reverse=True,
        )
        return render_template("bloods.html", bloods=bloods)

    app.register_blueprint(bloods_bp)

    register_user_page_menu_bar("Bloods", "/bloods") # register the bloods page in the menu bar

    # update the bloods after each request that could have affected
    @app.after_request
    def trigger_bloods_sync(response):
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
                    print(f"[Bloods Plugin] Sync Error: {e}")
        return response
    
    