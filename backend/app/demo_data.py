"""Realistic, clearly-labelled demo content for exercising the AI resume
screening end to end (first HR meeting § 16: never test the screening
against an empty or placeholder JD). Used by `python -m app.cli
seed-demo-job` and by scripts/verify_ai_screening.py.

Nothing here is real-person data; the job title carries "(Demo)" so it can
never be mistaken for a real opening.
"""

from app.models.job import EmploymentType

DEMO_JOB_TITLE = "Full Stack Developer – MERN (Demo)"
DEMO_JOB_DEPARTMENT = "Engineering"
DEMO_JOB_LOCATION = "Chennai, India (Hybrid)"
DEMO_JOB_EMPLOYMENT_TYPE = EmploymentType.FULL_TIME

DEMO_JOB_DESCRIPTION = """\
About the role
We are looking for a Full Stack Developer with hands-on MERN stack experience to build and \
maintain customer-facing web applications and the REST APIs behind them. You will work in a \
small cross-functional team with a product manager, a designer and QA, and own features from \
design discussion through production release.

Key responsibilities
- Design, build and maintain responsive single-page applications using React.js (hooks, \
context, React Router) and modern JavaScript (ES6+).
- Develop RESTful APIs and backend services with Node.js and Express.js, including \
authentication/authorization (JWT), input validation and error handling.
- Design MongoDB schemas and write efficient queries and aggregation pipelines (Mongoose ODM).
- Integrate frontend and backend, third-party APIs and payment/notification services.
- Write unit and integration tests (Jest, React Testing Library, Supertest) and take part in \
code reviews.
- Use Git for version control with a pull-request workflow; contribute to CI/CD pipelines.
- Troubleshoot production issues, improve performance and follow secure coding practices \
(OWASP Top 10).
- Collaborate in an Agile/Scrum team and document the features you build.

Required skills and experience
- 2+ years of professional experience as a full stack or MERN developer.
- Strong JavaScript (ES6+), HTML5 and CSS3.
- React.js in production: component design, state management (Redux or Context API), hooks.
- Node.js and Express.js: building and documenting REST APIs.
- MongoDB: schema design, indexing and aggregation.
- Git and GitHub/GitLab workflows.
- Understanding of REST principles, HTTP, and web application security basics.

Qualifications
- Bachelor's degree in Computer Science, Information Technology or a related field (B.E./ \
B.Tech/B.Sc/BCA/MCA), or equivalent practical experience.

Nice to have
- TypeScript, Next.js.
- Docker and a cloud platform (AWS, Azure or GCP).
- Experience with message queues, Redis or WebSockets.

What we offer
- Hybrid working from our Chennai office, mentoring from senior engineers and a structured \
learning budget.
"""
