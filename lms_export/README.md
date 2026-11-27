# LMS materials export

Run **Actions → Export LMS materials → Run workflow** after merging this change.
The default course is 231. Enter multiple course IDs separated by commas or spaces
to export multiple subjects. Uses the existing SKOLKOVO_EMAIL and SKOLKOVO_PASSWORD secrets.

Files are downloaded to `lms_export/materials/<course ID> - <subject>/`.
Each subject also includes readable page text and a manifest of downloaded files
and failures. Download the `lms-materials` artifact from the workflow run within 7 days.

Materials are deliberately ignored by Git because this repository is public.
Actions artifacts require authenticated repository access; anyone with repository
read access may be able to download them. Use a private repository for confidential materials.
The export does not publish files to the Git history, modify calendars, or send messages.

Supported: files linked through Moodle resources, folders, pages, books and assignments.
External services, embedded video streams and content requiring separate authorization
are not downloaded. A failed resource makes the run fail while preserving partial results.

Local use: install requirements, set the two credential variables and optionally
LMS_COURSE_IDS, then run `python lms_export/export.py`.

With no course IDs, the exporter discovers subjects from the user's grade overview (userid 4643). Grades themselves are not exported. A change to the exporter on main also starts an export.
