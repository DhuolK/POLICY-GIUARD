# Truehost cPanel Deployment Runbook for POLICYGUARD

This runbook describes how to deploy the POLICYGUARD application to Truehost Kenya shared hosting using cPanel, with no SSH access required.

## Prerequisites

- A Truehost Kenya shared hosting account with cPanel access.
- The POLICYGUARD source code (this repository).
- A local MySQL database dump (if migrating from MongoDB, see migration script).
- Domain name pointing to the hosting account (or use a temporary URL).

## Overview

The deployment involves:
1. Creating a MySQL database and user in cPanel.
2. Importing the database schema.
3. Configuring the Python application (Flask backend) via cPanel's "Setup Python App".
4. (Optional) Configuring the Node.js application if you have a separate frontend (note: this repo uses server-side rendered Flask templates, so there is no separate Node.js frontend).
5. Setting environment variables.
6. Uploading the source code.
7. Installing dependencies.
8. Restarting the application.
9. Verifying the deployment.

## Step-by-Step Instructions

### 1. [MANUAL] Create MySQL Database and User

1. Log in to cPanel.
2. Navigate to **MySQL® Databases**.
3. Under **Create New Database**, enter a database name (e.g., `policy_guard`). cPanel will prefix it with your username (e.g., `username_policy_guard`).
4. Click **Create Database**.
5. Under **MySQL Users**, create a new user (e.g., `policy_user`) and set a strong password.
6. Under **Add User to Database**, select the user and the database, then click **Add**.
7. Grant all privileges to the user for the database.
8. Note down:
   - Database name: `username_policy_guard`
   - Username: `username_policy_user`
   - Password: (the one you set)
   - Hostname: `localhost`

### 2. [MANUAL] Import Schema via phpMyAdmin

1. In cPanel, open **phpMyAdmin**.
2. Select the database you just created from the left sidebar.
3. Click the **Import** tab.
4. Under **File to Import**, choose the `scripts/schema.sql` file from this repository.
5. Leave the format as SQL and click **Go**.
6. Wait for the import to complete. You should see a success message and a list of tables created.

### 3. Prepare the Source Code

1. Ensure you have a clean copy of the POLICYGUARD repository.
2. If you are migrating from MongoDB, run the migration script now (see `scripts/migrate_mongo_to_mysql.py`) to transfer your data to the new MySQL database. Update the `MONGO_URI` and `SQLALCHEMY_DATABASE_URI` in your `.env` file as needed.
3. Remove or rename the `MongoDB` connection details from your `.env` if you are not keeping a fallback.

### 4. Set Up the Python Application

1. In cPanel, find the **Software** section and click **Setup Python App**.
2. Click **Create Application**.
3. Fill in the form:
   - **Python version**: Choose the latest available (3.10+).
   - **Application root**: `/home/username/policy_guard` (or the directory where you will place the code).
   - **Application URL**: `/` (or a subpath if desired).
   - **Application startup file**: `passenger_wsgi.py`
   - **Application entry point**: `application`
4. Click **Create**.

### 5. Upload the Source Code

You have two options: use Git (if enabled) or upload via File Manager.

#### Option A: Using Git (if Git Version Control is enabled in your plan)

1. In cPanel, under **Files**, click **Git Version Control**.
2. Click **Create**.
3. Clone URL: Enter your repository URL (e.g., `https://github.com/DhuolK/POLICY-GUARD.git`).
4. Directory: Enter the application root you set in step 4 (e.g., `policy_guard`).
5. Click **Create**.
6. Wait for the clone to finish, then click **Update to Commit** to get the latest changes.

#### Option B: Using File Manager

1. In cPanel, open **File Manager**.
2. Navigate to the application root directory (e.g., `home/username/policy_guard`).
3. Click **Upload** and select a ZIP archive of the POLICYGUARD repository (excluding the `.git` directory if you wish).
4. Extract the ZIP in place.
5. Ensure the files are placed directly in the application root (i.e., `passenger_wsgi.py` should be at the top level).

### 6. Configure Environment Variables

1. In the **Setup Python App** screen, locate the **Environment Variables** section.
2. Add the following variables (replace placeholders with your actual values):

   | Variable | Value |
   |----------|-------|
   | `FLASK_ENV` | `production` |
   | `SECRET_KEY` | A 32-character random string (generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` ) |
   | `SQLALCHEMY_DATABASE_URI` | `mysql+pymysql://username_policy_user:your_password@localhost/username_policy_guard` |
   | `APP_BASE_URL` | `https://your-domain.com` |
   | `AT_USERNAME` | (Your Africas Talking username) |
   | `AT_API_KEY` | (Your Africas Talking API key) |
   | `AT_SENDER_ID` | (Your Africas Talking sender ID) |
   | `SMS_SIMULATE` | `0` (set to `1` for testing without sending real SMS) |
   | `MPESA_ENV` | `production` (or `sandbox` for testing) |
   | `MPESA_CONSUMER_KEY` | (Your Daraja consumer key) |
   | `MPESA_CONSUMER_SECRET` | (Your Daraja consumer secret) |
   | `MPESA_BUSINESS_SHORT_CODE` | (Your Lipa na M-Pesa short code) |
   | `MPESA_LNM_ONLINE_URL` | `https://your-domain.com/mpesa/callback` |
   | `MPESA_LNM_VALIDATION_URL` | `https://your-domain.com/mpesa/validation` |
   | `MPESA_C2B_VALIDATION_URL` | `https://your-domain.com/mpesa/c2b/validate` |
   | `MPESA_C2B_CONFIRMATION_URL` | `https://your-domain.com/mpesa/c2b/confirm` |
   | `LOGIN_RATE_LIMIT` | `10 per minute` (adjust as needed) |
   | `MONGO_URI` | (Optional: keep for migration fallback) |
   | `MONGO_DB_NAME` | `policy_guard` |

3. Click **Save** and then **Build Application**.

### 7. Install Dependencies

1. In the **Setup Python App** screen, click the **Run Pip Install** button.
2. Wait for the installation to complete (this will install packages from `requirements.txt`).

### 8. Restart the Application

1. In the **Setup Python App** screen, click the **Restart Application** button.
2. Wait for the restart to complete.

### 9. Verify the Deployment

1. Visit your domain (or the application URL) in a browser.
2. You should see the login page or the dashboard (if already logged in).
3. To verify the health endpoint, navigate to `https://your-domain.com/health` (you may need to add this route; see the `/health` route in the code).
4. Log in with an admin or worker account (you may need to create one via the migration script or by using the `reset_admin.py` script).
5. Perform a smoke test:
   - Create a client.
   - Create a vehicle for that client.
   - Issue a policy.
   - Make a payment.
   - Check that the policy status updates correctly.

### 10. [MANUAL] Set Up Backups

1. In cPanel, under **Files**, click **Backup**.
2. Configure a full or partial backup schedule (daily, weekly, monthly) as desired.
3. Ensure that your MySQL database is included in the backup.

### 11. Rollback Plan

If something goes wrong after deployment:

1. Keep the MongoDB connection code intact (do not remove it) for one release cycle.
2. To revert to MongoDB, simply point the `SQLALCHEMY_DATABASE_URI` back to a MongoDB URI (if you have kept the PyMongo code) or restore the MongoDB dump.
3. Alternatively, restore the MySQL database from a backup taken before the migration.

## Troubleshooting

- **Application fails to start**: Check the application logs in cPanel under **Setup Python App** → **Log**.
- **Database connection errors**: Verify the `SQLALCHEMY_DATABASE_URI` and ensure the MySQL server is reachable (should be localhost).
- **Permission errors on files**: Ensure the application files are readable by the web server (usually they are fine after upload).
- **Missing dependencies**: Re-run **Run Pip Install** in Setup Python App.

## Notes

- This application uses server-side rendered Flask templates with Tailwind CSS and GSAP via CDN. There is no separate Node.js build step.
- If you wish to use a separate Next.js frontend in the future, you would need to set up a second application in cPanel (Setup Node.js App) and proxy requests accordingly.

## Completed By

[Your Name] - [Date]