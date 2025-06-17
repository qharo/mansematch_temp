# app/main.py
import json
from datetime import datetime
from typing import Optional, Dict, Any, Union, List
from pathlib import Path
from uuid import uuid4
import logging # Keep logging import at the top
import html
import os
from contextlib import asynccontextmanager # For lifespan manager

from fastapi import FastAPI, Request, Form, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# --- Logging Setup ---
# Configure logging as early as possible.
# Using asctime for ISO-like format, process ID, and thread ID for more context
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format='%(asctime)s.%(msecs)03dZ [%(process)d:%(thread)d] %(levelname)-5s [%(name)s] %(module)s.%(funcName)s: %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S' # Simplified ISO 8601 format
)
logger = logging.getLogger(__name__) # Logger for this module

# --- Import database components ---
# This import should now work correctly because `app` is a package
from .database import reports_collection, connect_to_mongo, close_mongo_connection

# --- Constants & Configuration ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
QUIZZES_DATA_FILE = BASE_DIR / "quizzes_data.json"


# --- FastAPI Lifespan Events for DB Connection ---
@asynccontextmanager
async def lifespan(app_instance: FastAPI): # Renamed 'app' to 'app_instance' to avoid conflict
    logger.info("Application startup: connecting to MongoDB...")
    connect_to_mongo() # Call the connect function from database.py
    yield
    logger.info("Application shutdown: closing MongoDB connection...")
    close_mongo_connection() # Call the close function from database.py

app = FastAPI(title="mansematch", lifespan=lifespan)


# Hardcoded users for POC
FAKE_USERS_DB = {
    "user1@example.com": {
        "hashed_password": "password123",
        "email": "user1@example.com",
        "id": "user1"
    },
    "user2@example.com": {
        "hashed_password": "password456",
        "email": "user2@example.com",
        "id": "user2"
    }
}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals['html_escape'] = html.escape
templates.env.filters['json_dumps'] = json.dumps

# --- BFI-10 Specific Data ---
BFI10_TRAIT_DESCRIPTIONS = {
    "Extraversion": "Reflects tendency to be sociable, assertive, and energetic vs. reserved and quiet.",
    "Agreeableness": "Reflects tendency to be compassionate, cooperative, and kind vs. antagonistic and critical.",
    "Conscientiousness": "Reflects tendency to be organized, dependable, and responsible vs. careless and impulsive.",
    "Neuroticism": "Reflects tendency to experience negative emotions, such as anxiety and sadness (Emotional Stability is the inverse).",
    "Openness": "Reflects tendency to be imaginative, curious, and open to new experiences vs. conventional and preferring routine."
}

def get_bfi10_interpretation_details(trait_name: str, score: Optional[float]) -> Dict[str, str]:
    level = "N/A"
    level_description = "Score not available or trait not applicable."
    if score is not None:
        if score < 2.5:
            level = "Low"
            if trait_name == "Neuroticism":
                level_description = "Indicates a tendency to be calm, emotionally stable, and resilient to stress."
            else:
                level_description = f"Indicates a lower expression of typical {trait_name.lower()} characteristics."
        elif score <= 3.5:
            level = "Average"
            level_description = f"Indicates a moderate or balanced expression of typical {trait_name.lower()} characteristics."
        else:  # score > 3.5
            level = "High"
            if trait_name == "Neuroticism":
                level_description = "Indicates a tendency to experience emotional fluctuations, anxiety, or moodiness more frequently."
            else:
                level_description = f"Indicates a higher expression of typical {trait_name.lower()} characteristics."
    else: # Explicitly handle case where score is None
        level = "N/A" # or "Not Scored"
        level_description = "This trait was not scored."


    general_description = BFI10_TRAIT_DESCRIPTIONS.get(trait_name, "General description not available.")

    return {
        "level": level,
        "level_specific_description": level_description,
        "general_trait_description": general_description
    }


# --- Custom Exception for HTMX Redirects ---
class HtmxRedirectException(HTTPException):
    def __init__(self, redirect_url: str):
        super().__init__(status_code=200, detail="HTMX redirect") # 200 is often used for HTMX redirects
        self.headers = {"HX-Redirect": redirect_url}

@app.exception_handler(HtmxRedirectException)
async def htmx_redirect_exception_handler(request: Request, exc: HtmxRedirectException):
    return Response(content=exc.detail, status_code=exc.status_code, headers=exc.headers)


# --- Helper Functions ---
def load_quizzes_data() -> Dict[str, Any]:
    try:
        with open(QUIZZES_DATA_FILE, 'r') as f:
            data = json.load(f)
            logger.debug(f"Successfully loaded quiz definitions from {QUIZZES_DATA_FILE}")
            return data
    except FileNotFoundError:
        logger.error(f"{QUIZZES_DATA_FILE} not found. Returning empty quiz definitions.")
        return {"quizzes": []}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding {QUIZZES_DATA_FILE}: {e}. Returning empty quiz definitions.")
        return {"quizzes": []}


# --- Authentication Dependencies ---
async def get_current_user_from_cookie(request: Request) -> Optional[Dict[str, Any]]:
    user_email = request.cookies.get("user_session")
    if user_email and user_email in FAKE_USERS_DB:
        return FAKE_USERS_DB[user_email]
    return None

async def get_current_user_or_htmx_redirect(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_from_cookie)
) -> Dict[str, Any]:
    if not current_user:
        logger.warning(f"User not authenticated. Path: {request.url.path}. HX-Request: {'hx-request' in request.headers}")
        if "hx-request" in request.headers:
            raise HtmxRedirectException(redirect_url=app.url_path_for("auth_page_route"))
        else: # Standard redirect for non-HTMX requests
            # Using 307 to preserve method if original was POST, though for GET this is fine
            raise HTTPException(status_code=307, detail="Not authenticated", headers={"Location": app.url_path_for("auth_page_route")})
    return current_user


# --- Middleware for common template variables ---
@app.middleware("http")
async def common_template_vars_middleware(request: Request, call_next):
    request.state.user = await get_current_user_from_cookie(request)
    request.state.current_year = datetime.now().year
    response = await call_next(request)
    return response


# --- Routes ---

@app.get("/", response_class=HTMLResponse, name="homepage")
async def homepage_route(request: Request):
    logger.info(f"Homepage requested by user: {request.state.user.get('email') if request.state.user else 'Anonymous'}")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "title": "Welcome - mansematch"
    })

@app.post("/subscribe", name="subscribe")
async def subscribe_email_route(request: Request, email: str = Form(...)):
    logger.info(f"New email subscription: {email}")
    escaped_email = html.escape(email)
    return HTMLResponse(f"<p class='text-green-600 font-semibold'>Thank you for subscribing, {escaped_email}!</p>")


@app.get("/auth", response_class=HTMLResponse, name="auth_page_route")
async def auth_page_route(request: Request):
    if request.state.user:
        logger.info(f"User {request.state.user['email']} already authenticated, redirecting to dashboard.")
        # Using 303 See Other for redirect after GET if resource has moved or state changes
        return RedirectResponse(url=app.url_path_for("dashboard_page_route"), status_code=303)
    logger.info("Auth page requested.")
    return templates.TemplateResponse("auth.html", {
        "request": request,
        "title": "Sign In / Sign Up"
    })

@app.post("/login", name="login_route")
async def login_user_route(request: Request, email: str = Form(...), password: str = Form(...)):
    user = FAKE_USERS_DB.get(email)
    if not user or user["hashed_password"] != password:
        logger.warning(f"Login failed for email: {email}")
        return HTMLResponse("<p class='text-red-600'>Invalid email or password. Please try again.</p>", status_code=401)

    logger.info(f"User {email} logged in successfully.")
    response = HTMLResponse(content="<p>Login successful! Redirecting...</p>", status_code=200) # Keep 200 for HTMX
    response.set_cookie(
        key="user_session",
        value=user["email"],
        httponly=True,
        max_age=1800, # 30 minutes
        samesite="Lax", # Good default
        secure=request.url.scheme == "https", # Only send over HTTPS if applicable
        path="/"
    )
    redirect_url = app.url_path_for("dashboard_page_route")
    response.headers["HX-Redirect"] = redirect_url
    logger.debug(f"Login successful, HX-Redirecting to: {redirect_url}")
    return response

@app.post("/logout", name="logout_route")
async def logout_user_route(request: Request): # No current_user dependency needed here as we are logging out
    user_email_before_logout = request.state.user.get('email') if request.state.user else "Unknown (already logged out or session expired)"
    logger.info(f"User {user_email_before_logout} initiating logout.")

    # Prepare context for the page we are redirecting to (homepage)
    # The middleware will re-evaluate user for the template, but we want to ensure
    # the user is None for the template rendered in *this* response.
    context = {
        "request": request,
        "title": "Welcome - mansematch",
        "user": None # Explicitly set user to None for rendering index.html
    }
    html_content = templates.get_template("index.html").render(context)

    response = HTMLResponse(content=html_content)
    response.delete_cookie(
        "user_session",
        httponly=True,
        samesite="Lax",
        secure=request.url.scheme == "https",
        path="/"
    )
    # HX-Push-Url updates the browser's URL bar.
    # Since we are returning the full new page content for "/", this is good.
    response.headers["HX-Push-Url"] = app.url_path_for("homepage")
    return response


@app.get("/dashboard", response_class=HTMLResponse, name="dashboard_page_route")
async def dashboard_page_route(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)
):
    logger.info(f"Dashboard requested by user: {current_user['email']}")
    quizzes_definitions = load_quizzes_data().get("quizzes", [])

    user_reports = []
    if reports_collection is not None:
        try:
            report_cursor = reports_collection.find({"user_id": current_user["id"]}).sort("date_taken", -1)
            user_reports = list(report_cursor)
            logger.debug(f"User {current_user['email']} has {len(user_reports)} reports from MongoDB.")
        except Exception as e:
            logger.error(f"Error fetching reports from MongoDB for user {current_user['email']}: {e}")
            # Consider raising HTTPException or passing an error to the template
            # For now, it will show "no reports" if DB fails.
    else:
        logger.warning("Reports collection is not available. Cannot fetch reports for dashboard.")

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "title": "Dashboard - mansematch",
        "user": current_user,
        "quizzes": quizzes_definitions,
        "reports": user_reports
    })

# --- Quiz Routes ---
@app.get("/quiz/{quiz_id}", response_class=HTMLResponse, name="quiz_page_route")
async def quiz_page_route(
    request: Request,
    quiz_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)
):
    logger.info(f"Quiz page requested for quiz_id: {quiz_id} by user: {current_user['email']}")
    all_quiz_definitions = load_quizzes_data()
    quiz_detail = next((q for q in all_quiz_definitions.get("quizzes", []) if q["id"] == quiz_id), None)

    if not quiz_detail:
        logger.warning(f"Quiz ID: {quiz_id} not found when requested by user {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Quiz ID: {quiz_id} not found.")

    if "questions" not in quiz_detail:
        logger.debug(f"Quiz ID: {quiz_id} has no 'questions' key. Defaulting to empty list.")
        quiz_detail["questions"] = []

    logger.debug(f"Quiz detail for quiz_id {quiz_id} being sent to template (summary): title='{quiz_detail.get('title')}', num_questions={len(quiz_detail.get('questions',[]))}")

    return templates.TemplateResponse("quiz_page.html", {
        "request": request,
        "title": f"Quiz: {html.escape(quiz_detail.get('title', 'Untitled Quiz'))}",
        "user": current_user,
        "quiz": quiz_detail
    })



def calculate_bfi10_scores(questions: List[Dict[str, Any]], user_answers_dict: Dict[str, Union[int, float]]) -> Dict[str, Optional[float]]:
    scores: Dict[str, List[float]] = {"E": [], "A": [], "C": [], "N": [], "O": []}
    trait_map = {
        "E": "Extraversion", "A": "Agreeableness", "C": "Conscientiousness",
        "N": "Neuroticism", "O": "Openness"
    }

    for question in questions:
        q_id_str = str(question.get("id"))
        key = question.get("key")
        user_answer_val = user_answers_dict.get(q_id_str)

        if user_answer_val is None or key is None:
            logger.warning(f"Missing answer or key for BFI-10 question ID: {q_id_str}. User answers: {user_answers_dict}")
            continue

        # Ensure user_answer_val is float for calculations
        try:
            answer_val = float(user_answer_val)
        except (ValueError, TypeError):
            logger.error(f"Invalid non-numeric answer for BFI-10 question {q_id_str}: {user_answer_val}")
            continue

        trait_code = key[0]
        is_reversed = key.endswith("_R")

        if trait_code not in scores:
            logger.warning(f"Unknown trait '{trait_code}' in key '{key}' for question {q_id_str}")
            continue

        actual_score = (6 - answer_val) if is_reversed else answer_val
        scores[trait_code].append(actual_score)

    final_scores: Dict[str, Optional[float]] = {}
    for trait_code, items_scores in scores.items():
        trait_name = trait_map.get(trait_code)
        if not trait_name:
            logger.warning(f"Trait code {trait_code} not found in trait_map.")
            continue
        if items_scores:
            avg_score = sum(items_scores) / len(items_scores)
            final_scores[trait_name] = round(avg_score, 2)
        else:
            final_scores[trait_name] = None
            logger.warning(f"No valid scores recorded for BFI-10 trait: {trait_name} (Code: {trait_code})")
    return final_scores

@app.post("/quiz/{quiz_id}/submit", name="submit_quiz_route")
async def submit_quiz_route(
    request: Request,
    quiz_id: str,
    answers: str = Form(...), # answers is a JSON string from the form
    current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)
):
    logger.info(f"Quiz submission for quiz_id: {quiz_id} by user: {current_user['email']}")
    logger.debug(f"Raw answers JSON string from form: {answers}")

    if reports_collection is None:
        logger.error("Reports collection is not available. Cannot save quiz submission.")
        raise HTTPException(status_code=503, detail="Database service unavailable. Cannot save quiz results.")

    all_quiz_definitions = load_quizzes_data()
    quiz_detail = next((q for q in all_quiz_definitions.get("quizzes", []) if q["id"] == quiz_id), None)

    if not quiz_detail:
        logger.error(f"Quiz ID: {quiz_id} not found during submission by user {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Quiz ID: {quiz_id} not found.")

    try:
        # user_answers_dict will have string keys, and values can be numbers or strings
        user_answers_dict: Dict[str, Any] = json.loads(answers)
        logger.debug(f"Parsed user answers: {user_answers_dict}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid answers JSON format from user {current_user['email']} for quiz {quiz_id}: {e}")
        raise HTTPException(status_code=400, detail="Invalid answers format.")

    quiz_questions = quiz_detail.get("questions", [])
    report_score: Union[str, Dict[str, Optional[float]]]
    report_type = "standard" # Default report type

    if quiz_id == "bfi-10":
        report_type = "bfi-10"
        # Ensure BFI-10 answers are numeric, as expected by calculate_bfi10_scores
        bfi_numeric_answers: Dict[str, Union[int, float]] = {}
        all_answers_valid = True
        for q_def in quiz_questions:
            q_id_str = str(q_def.get("id"))
            ans_val = user_answers_dict.get(q_id_str)
            try:
                bfi_numeric_answers[q_id_str] = float(ans_val) # Convert to float
            except (ValueError, TypeError):
                logger.error(f"BFI-10 answer for Q_ID {q_id_str} is not convertible to float: {ans_val}. User: {current_user['email']}")
                all_answers_valid = False
                # Optionally, you might want to assign a default or skip, but erroring out is safer if all answers are required.
                # For now, calculate_bfi10_scores will skip invalid ones.

        # If strict validation is needed:
        # if not all_answers_valid:
        #     raise HTTPException(status_code=400, detail="One or more BFI-10 answers were not valid numbers.")

        report_score = calculate_bfi10_scores(quiz_questions, bfi_numeric_answers)
        logger.info(f"User {current_user['email']} BFI-10 scores: {report_score}")

    else: # Standard quiz scoring
        correct_answers_count = 0
        total_questions = len(quiz_questions)
        if total_questions > 0:
            for question in quiz_questions:
                question_id_str = str(question.get("id"))
                user_answer = user_answers_dict.get(question_id_str) # User answer could be string or number from JSON
                correct_answer = question.get("answer") # Correct answer from quizzes_data.json
                # Compare as strings to handle type discrepancies (e.g., "8" vs 8)
                if user_answer is not None and str(user_answer) == str(correct_answer):
                    correct_answers_count += 1
            score_percentage = (correct_answers_count / total_questions) * 100
        else:
            score_percentage = 0
        report_score = f"{score_percentage:.0f}%" # Format as whole number percentage string
        logger.info(f"User {current_user['email']} scored {report_score} on quiz {quiz_id}.")

    new_report_id = f"rep_{uuid4().hex[:10]}"
    new_report_doc = {
        "id": new_report_id,
        "user_id": current_user["id"],
        "quiz_id": quiz_id,
        "quiz_title": quiz_detail.get('title', 'Untitled Quiz'),
        "quiz_description": quiz_detail.get('description', 'No description available.'),
        "score": report_score, # Can be Dict for BFI-10 or string for others
        "date_taken": datetime.utcnow(),
        "answers_submitted": user_answers_dict, # Store the original parsed answers
        "report_type": report_type
    }

    try:
        insert_result = reports_collection.insert_one(new_report_doc)
        # PyMongo's insert_one returns InsertOneResult, _id is on insert_result.inserted_id
        logger.info(f"New report {new_report_id} (MongoDB _id: {insert_result.inserted_id}) saved for user {current_user['email']}.")
    except Exception as e:
        logger.error(f"Error saving report {new_report_id} to MongoDB for user {current_user['email']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save quiz results due to a database error.")

    # Redirect to the specific report page using the custom 'id'
    report_page_url = app.url_path_for("report_page_route", report_id=new_report_id)
    logger.info(f"Redirecting user {current_user['email']} to report page: {report_page_url}")
    # Using 303 See Other for redirect after POST
    return RedirectResponse(url=report_page_url, status_code=303)

@app.get("/healthz", status_code=200)
async def health_check_route():
    return {"status": "ok"}

@app.get("/report/{report_id}", name="report_page_route")
async def report_page_route(
    request: Request,
    report_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)
):
    logger.info(f"User {current_user['email']} requesting report page for report_id: {report_id}")

    if reports_collection is None:
        logger.error(f"Reports collection unavailable when trying to view report {report_id}")
        raise HTTPException(status_code=503, detail="Database service unavailable. Cannot load report.")

    try:
        # Query using the custom 'id' field, not MongoDB's '_id'
        report_detail = reports_collection.find_one({"id": report_id, "user_id": current_user["id"]})
    except Exception as e:
        logger.error(f"Error fetching report {report_id} from MongoDB for user {current_user['email']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load report due to a database error.")

    if not report_detail:
        logger.warning(f"Report ID: {report_id} not found or access denied for user {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Report ID: {report_id} not found or access denied.")

    context = {
        "request": request,
        "title": f"Report: {html.escape(report_detail.get('quiz_title', 'Quiz Report'))}",
        "user": current_user,
        "report": report_detail # Pass the full report document
    }

    if report_detail.get("report_type") == "bfi-10" and isinstance(report_detail.get("score"), dict):
        trait_scores: Dict[str, Optional[float]] = report_detail["score"]
        radar_labels = ["Extraversion", "Agreeableness", "Conscientiousness", "Neuroticism", "Openness"]
        # Handle None scores for radar data by defaulting to 0 or another placeholder
        radar_data = [trait_scores.get(label, 0.0) if trait_scores.get(label) is not None else 0.0 for label in radar_labels]

        context["radar_chart_labels"] = radar_labels
        context["radar_chart_data"] = radar_data

        table_data = []
        for trait in radar_labels:
            score_value = trait_scores.get(trait) # This can be float or None
            interpretation = get_bfi10_interpretation_details(trait, score_value)
            table_data.append({
                "trait": trait,
                "score": score_value if score_value is not None else "N/A",
                "interpretation_level": interpretation["level"],
                "interpretation_description": interpretation["level_specific_description"],
                "general_trait_description": interpretation["general_trait_description"]
            })
        context["bfi_table_data"] = table_data
        logger.debug(f"Prepared BFI-10 report data for {report_id}: Scores {trait_scores}, Radar Data {radar_data}")
        return templates.TemplateResponse("big_five_report.html", context)
    else:
        logger.debug(f"Displaying generic report for {report_id}")
        return templates.TemplateResponse("report_page.html", context)


# --- Main Execution (for local development) ---
if __name__ == "__main__":
    import uvicorn
    # This check is informative for local runs; DB connection status is handled by lifespan.
    logger.info("Attempting to run Uvicorn for local development...")
    if os.getenv("MONGO_URI") and reports_collection is None and app.router.lifespan_context:
         # If MONGO_URI is set, we expect a connection. If reports_collection is still None
         # after lifespan should have run (or tried to run connect_to_mongo on import if lifespan isn't used),
         # it means connection likely failed.
         logger.warning(
            "MongoDB URI is set, but reports_collection is None. "
            "Database features might be unavailable if connection failed during startup."
        )
    elif not os.getenv("MONGO_URI"):
        logger.warning("MONGO_URI not set. MongoDB features will be disabled.")


    # For local development, it's fine to use reload.
    # The Uvicorn command in Dockerfile is what's used for deployment.
    uvicorn.run(
        "app.main:app", # Correctly reference the app instance within the package
        host=os.getenv("HOST", "0.0.0.0"), # HOST for Render
        port=int(os.getenv("PORT", 8000)), # PORT for Render
        reload=True, # Enable reload for local development
        reload_dirs=[str(BASE_DIR.parent)], # Watch the parent of 'app' (project root)
        log_level=os.getenv("LOG_LEVEL", "info").lower()
    )
