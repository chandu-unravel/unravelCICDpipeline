import sys
from datetime import timedelta, datetime
import json
import re
import requests
import getopt
import urllib3
import os
import time
import html
from jira import JIRA
from bs4 import BeautifulSoup
import markdown
import base64

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
http = urllib3.PoolManager(cert_reqs='CERT_NONE')
headers = {'Content-Type': 'application/json'}
# %%
# DBRKS URL pattern
pattern = r"^https://adb-([0-9]+).([0-9]+).azuredatabricks.net/\?o=([0-9]+)#job/([0-9]+)/run/([0-9]+)$"
pattern_as_text = r"https://adb-([0-9]+).([0-9]+).azuredatabricks.net/\?o=([0-9]+)#job/([0-9]+)/run/([0-9]+)"
cleanRe = re.compile("<.*?>")

app_summary_map = {}
app_summary_map_list = []

events_map = {
    "efficiency": "Insights to make this app resource/cost efficient",
    "appFailure": "Insights to help with failure analysis",
    "Bottlenecks": "Insights to make this app faster",
    "SLA": "Insights to make this app meet SLA",
}

# Git specific variables
pr_number = os.getenv("PR_NUMBER")
repo_name = os.getenv("GITHUB_REPOSITORY")
# access_token = os.getenv("GITHUB_TOKEN")
access_token = os.getenv("GIT_TOKEN")
pr_url = os.getenv("PR_URL")
pr_user_email = os.getenv("PR_USER_EMAIL")
pr_commit_id = os.getenv("COMMIT_SHA")
pr_base_branch = os.getenv("BASE_BRANCH")
pr_target_branch = os.getenv("TARGET_BRANCH")

# Unravel specific variables
unravel_url = os.getenv("UNRAVEL_URL")
unravel_token = os.getenv("UNRAVEL_JWT_TOKEN")

# Slack specific variables
slack_webhook = os.getenv("SLACK_WEBHOOK")

# Jira specific variables
domain = os.getenv("JIRA_DOMAIN")
email = os.getenv("JIRA_EMAIL")
project_key = os.getenv("JIRA_PROJECT_KEY")
api_token = os.getenv("JIRA_API_TOKEN")

# if "https://" in unravel_url:
#     lr_url = unravel_url.replace("https://", "http://") + ":4043"
# else:
#     lr_url = unravel_url + ":4043"
lr_url = "http://18.204.206.1:4043"

file_code_map = {}
file_code_map['df.toPandas()'] = 'toPandas() moves all the data to driver to convert the spark df to a pandas dataframe.\n\n Instead use this statement df.withColumn("<newColumn>", lit("<constant_value>"))'
file_code_map['df.collect()'] = 'Avoid collecting all the data if we require only few rows of dataframe..\n\n Instead use this statement df.take(n)'



# %%
def get_api(api_url, api_token):
    response = requests.get(api_url, verify=False, headers={"Authorization": api_token})
    print(response.content)
    json_obj = json.loads(response.content)
    return json_obj


def check_response_on_get(json_val):
    if "message" in json_val:
        if json_val["message"] == "INVALID_TOKEN":
            raise ValueError("INVALID_TOKEN")


# %%
def search_summary_by_globalsearchpattern(
    base_url, api_token, start_time, end_time, gsp
):
    api_url = base_url + "/api/v1/ds/api/v1/databricks/runs/" + gsp + "/tasks/summary"
    print("URL: " + api_url)
    json_val = get_api(api_url, api_token)
    check_response_on_get(json_val)
    return json_val


# %%
def search_analysis(base_url, api_token, clusterUId, id):
    api_url = base_url + "/api/v1/spark/" + clusterUId + "/" + id + "/analysis"
    print("URL: " + api_url)
    json_val = get_api(api_url, api_token)
    check_response_on_get(json_val)
    return json_val


def search_summary(base_url, api_token, clusterUId, id):
    api_url = base_url + "/api/v1/spark/" + clusterUId + "/" + id + "/appsummary"
    print("URL: " + api_url)
    json_val = get_api(api_url, api_token)
    check_response_on_get(json_val)
    return json_val


# %%
def get_job_runs_from_description(pr_id, description_json):
    job_run_list = []
    for run_url in description_json["runs"]:
        match = re.search(pattern, run_url)
        if match:
            print(run_url)
            workspace_id = match.group(3)
            job_id = match.group(4)
            run_id = match.group(5)
            job_run_list.append(
                {
                    "pr_id": pr_id,
                    "pdbrks_url": run_url,
                    "workspace_id": workspace_id,
                    "job_id": job_id,
                    "run_id": run_id,
                }
            )

    return job_run_list


# %%
def get_job_runs_from_description_as_text(pr_id, description_text):
    job_run_list = []
    print("Description:\n" + description_text)
    print("Patten: " + pattern_as_text)
    matches = re.findall(pattern_as_text, description_text)
    if matches:
        for match in matches:
            workspace_id = match[2]
            job_id = match[3]
            run_id = match[4]
            job_run_list.append(
                {
                    "pr_id": pr_id,
                    "workspace_id": workspace_id,
                    "job_id": job_id,
                    "run_id": run_id,
                }
            )
    else:
        print("no match")
    return job_run_list


# %%
def get_organization_connection(organization_url, personal_access_token):
    credentials = BasicAuthentication("", personal_access_token)
    connection = Connection(base_url=organization_url, creds=credentials)
    return connection


def fetch_app_summary(unravel_url, unravel_token, clusterUId, appId):
    app_summary_map_for_git_comment = {}
    app_summary_map_for_jira_comments = {}
    autoscale_dict = {}
    summary_dict = search_summary(unravel_url, unravel_token, clusterUId, appId)
    summary_dict = summary_dict["annotation"]
    url = "{}/#/app/application/spark?execId={}&clusterUid={}".format(
        unravel_url, appId, clusterUId
    )
    app_summary_map_for_git_comment["Spark App"] = "[{}]({})".format(appId, url)
    app_summary_map_for_jira_comments["Spark App"] = "[{}|{}]".format(appId, url)
    cluster_url = "{}/#/compute/cluster_summary?cluster_uid={}&app_id={}".format(
        unravel_url, clusterUId, appId
    )
    app_summary_map_for_git_comment["Cluster"] = "[{}]({})".format(
        clusterUId, cluster_url
    )
    app_summary_map_for_jira_comments["Cluster"] = "[{}|{}]".format(
        clusterUId, cluster_url
    )

    estimated_cost = summary_dict["cents"] + summary_dict["dbuCost"]

    app_summary_map_for_git_comment["Estimated cost"] = "$ {}".format(
        round(estimated_cost, 3)
    )
    app_summary_map_for_jira_comments["Estimated cost"] = "$ {}".format(
        round(estimated_cost, 3)
    )
    runinfo = json.loads(summary_dict["runInfo"])
    app_summary_map_for_git_comment["Executor Node Type"] = runinfo["node_type_id"]
    app_summary_map_for_jira_comments["Executor Node Type"] = runinfo["node_type_id"]
    app_summary_map_for_git_comment["Driver Node Type"] = runinfo["driver_node_type_id"]
    app_summary_map_for_jira_comments["Driver Node Type"] = runinfo[
        "driver_node_type_id"
    ]
    app_summary_map_for_git_comment["Tags"] = runinfo["default_tags"]
    # tags = json.loads(runinfo["default_tags"])
    cluster_name = runinfo["default_tags"]['ClusterName']
    app_summary_map_for_jira_comments["Tags"] = runinfo["default_tags"]
    if "custom_tags" in runinfo.keys():
        app_summary_map_for_git_comment["Tags"] = {
            **app_summary_map_for_git_comment["Tags"],
            **runinfo["default_tags"],
        }
    if "autoscale" in runinfo.keys():
        autoscale_dict["autoscale_min_workers"] = runinfo["autoscale"]["min_workers"]
        autoscale_dict["autoscale_max_workers"] = runinfo["autoscale"]["max_workers"]
        autoscale_dict["autoscale_target_workers"] = runinfo["autoscale"][
            "target_workers"
        ]
        app_summary_map_for_git_comment["Autoscale"] = autoscale_dict
        app_summary_map_for_jira_comments["Autoscale"] = autoscale_dict
    else:
        app_summary_map_for_git_comment["Autoscale"] = "Autoscale is not enabled."
        app_summary_map_for_jira_comments["Autoscale"] = "Autoscale is not enabled."
    return app_summary_map_for_git_comment, app_summary_map_for_jira_comments, cluster_name


def get_pr_description():
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    pr_data = response.json()
    description = pr_data["body"]
    return description


def send_markdown_to_slack(channel, message):
    payload = {"channel": channel, "text": message, "mrkdwn": True}
    response = requests.post(slack_webhook, json=payload)
    if response.status_code == 200:
        print("Message sent successfully to Slack!")
    else:
        print(f"Failed to send message to Slack. Error: {response.text}")


def raise_jira_ticket(message):
    # Connect to Jira
    jira = JIRA(server="https://{}".format(domain), basic_auth=(email, api_token))

    # Create the issue
    issue_data = {
        "project": {"key": "CICD"},
        "summary": "Issue summary",
        "description": message,
        "issuetype": {"name": "Task"},
    }

    new_issue = jira.create_issue(fields=issue_data)

    print(new_issue)

    jira_link = "https://{}/browse/{}".format(domain, new_issue)

    return jira_link


def create_markdown_from_html(html_string):
    # Parse the HTML string
    soup = BeautifulSoup(html_string, "html.parser")

    # Find all <li> tags
    li_tags = soup.find_all("li")

    # Extract the text from each <li> tag
    bullet_points = [li.get_text(strip=True) for li in li_tags]

    # Convert bullet points to Markdown
    markdown_text = "\n".join([f"- {point}" for point in bullet_points])

    # Print the Markdown text
    print(markdown_text)

    return markdown_text


def get_pr_reviewers_list():
    # Set the GitHub access token

    # Set the API endpoint
    api_endpoint = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"

    # Set the request headers
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Send the API request
    response = requests.get(api_endpoint, headers=headers)

    # Check the response status code
    if response.status_code == 200:
        # Parse the response JSON
        pr_data = response.json()

        # Get the list of reviewers
        reviewers = [reviewer["login"] for reviewer in pr_data["requested_reviewers"]]

        # Print the reviewers
        print("Reviewers:", reviewers)
        return reviewers
    else:
        print("Failed to fetch pull request details:", response.text)
        return []


def create_jira_message(job_run_result_list):
    comments = ""
    comments += "----\n"
    comments += "This Issue was automatically created by Unravel to follow up on the insights generated for the runs of the jobs mentioned in the description of pr number {} was raised to merge {} from {} to {}\n".format(
        pr_number, pr_commit_id, pr_base_branch, pr_target_branch
    )
    if job_run_result_list:
        for r in job_run_result_list:
            comments += "----\n"
            comments += "Workspace Id: {}, Job Id: {}, Run Id: {}\n\n".format(
                r["workspace_id"], r["job_id"], r["run_id"]
            )
            comments += "----\n"
            comments += "[{}|{}]\n".format("Unravel URL", r["unravel_url"])

            if r["jir_app_summary"]:
                headers = list(r["jir_app_summary"].keys())
                header_row = "|| " + " || ".join(headers) + " |\n"
                data_rows = (
                    "| "
                    + " | ".join(str(r["jir_app_summary"].get(h, "")) for h in headers)
                    + " |\n"
                )
                comments += "----\n"
                comments += "App Summary\n"
                comments += "*Estimated cost is the sum of DBUs and VM Cost\n"
                comments += "----\n"
                comments += "\n" + header_row + data_rows

            if r["unravel_insights"]:
                comments += "\nUnravel Insights\n"
                comments += "----\n"
                for insight in r["unravel_insights"]:
                    categories = insight["categories"]
                    if categories:
                        for k in categories.keys():
                            instances = categories[k]["instances"]
                            if instances:
                                for i in instances:
                                    if i["key"].upper() != "SPARKAPPTIMEREPORT":
                                        comments += "\n"
                                        comments += "|| {}: {} ||\n".format(
                                            i["key"].upper(), events_map[i["key"]]
                                        )
                                        comments += "| ℹ️ *{}* 	|\n".format(i["title"])
                                        comments += "| ⚡ *Details*\n{}	|\n".format(
                                            i["events"]
                                        )
                                        if "<li>" in i["actions"]:
                                            comments += "| 🛠 *Actions*\n{}	|\n".format(
                                                create_markdown_from_html(i["actions"])
                                            )
                                        else:
                                            comments += "| 🛠 *Actions*\n{}	|\n".format(
                                                i["actions"]
                                            )
                                        comments += "\n"
    return comments


def search_string_in_code(code, search_string):
    line_numbers = []
    lines = code.split("\n")
    for line_number, line in enumerate(lines, 1):
        if search_string in line:
            line_numbers.append(line_number)

    all_lines = []
    for line_number in line_numbers:
        result = []
        start_line = max(1, line_number - 3)
        end_line = min(len(lines), line_number)
        for i in range(start_line, end_line + 1):
            result.append(f"{i} {lines[i - 1]}")
        all_lines.append(result)
    return line_numbers, all_lines

def create_custom_code_block_and_add_pr_comment(code_block):
    comment = "\n```python\n"
    for code in code_block:
        comment += "{}\n".format(code)
    comment += "```\n\n"
    return comment

def perform_code_review(get_file_name_flag=False):
    # Get the changed file paths from the pull request event payload
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    url = f'https://api.github.com/repos/{repo_name}/pulls/{pr_number}/files'
    response = requests.get(url, headers=headers)
    files = response.json()
    changed_files = [file['filename'] for file in files]
    if get_file_name_flag == True:
        return changed_files
    print(changed_files)

    file_contents = {}
    for file in files:
        file_url = file['raw_url']
        file_response = requests.get(file_url)
        file_content = file_response.text
        file_contents[file['filename']] = file_content
    # Personal access token (replace with your own token)


    for file_name, file_content in file_contents.items():
        for pattern, optimal_value in file_code_map.items():
            line_numbers, code_lines = search_string_in_code(file_content, pattern)

            # API endpoint
            url = f'https://api.github.com/repos/{repo_name}/pulls/{pr_number}/comments'

            # Request headers
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/vnd.github.v3+json',
                'X-GitHub-Api-Version': '2022-11-28'
            }

            for line_number, code_block in zip(line_numbers,code_lines):

                # Request body
                data = {
                    'body': optimal_value,
                    'path': file_name,
                    'commit_id': pr_commit_id,
                    'line': line_number
                }

                # Send POST request
                response = requests.post(url, headers=headers, data=json.dumps(data))

                # Check response status
                if response.status_code == 201:
                    print('Comment added successfully.')
                else:
                    comment = create_custom_code_block_and_add_pr_comment(code_block)
                    comment += "\n\n"
                    comment += optimal_value
                    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"

                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github.v3+json",
                    }
                    payload = {"body": "{}".format(comment)}
                    response = requests.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    
# d = '''{            "eventName": "CICDUsageEvent",
#                     "eventType": "CICD",
#                     "eventNumber": 4793,
#                     "eventTime": 1696896310332,
#                     "entityGroup": 2,
#                     "entityType": 2,
#                     "entityId": "8122943658466030_259557806380750_3341164-114",
#                     "user": "null",
#                     "queue": "null",
#                     "clusterName": "job-259557806380750-run-3341164",
#                     "clusterUid": "1013-000816-6ap9chzt",
#                     "staticRank": 20,
#                     "dynamicRank": 20.0,
#                     "title": "",
#                     "detail": "",
#                     "actions": ""
# }'''

def index_for_timestamp(prefix, ts):
    timestamp_seconds = ts / 1000
    ts = datetime.utcfromtimestamp(timestamp_seconds)
    if type(ts) == str:
        ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
    year = ts.strftime("%Y")
    month = ts.strftime("%m")
    day = ts.strftime("%d")
    d = datetime(int(year), int(month), int(day)).toordinal()
    sunday = str(datetime.fromordinal(d - (d % 7)))[8:10]
    saturday = str(datetime.fromordinal(d - (d % 7) + 6))[8:10]
    return prefix + year + month + sunday + "_" + saturday

def send_update_to_unravel(notification_sent,user_ids,jira_link,pr_url,pr_number,repo_name,es_document_list):
    detail_dict = {}
    detail_dict['agent'] = "GitHub"
    detail_dict['repository'] = repo_name
    detail_dict['prNumber'] = pr_number
    detail_dict['reviewers'] = user_ids
    detail_dict['prLink'] = pr_url
    detail_dict['fileDiff'] = perform_code_review(get_file_name_flag=True)
    detail_dict['imsLink'] = jira_link
    detail_dict['notificationSent'] = notification_sent
    for documents in es_document_list:
        documents['detail'] = str(detail_dict)
        id = documents['job']
        del documents['job']
        event_time = documents['eventTime']
        documents = json.dumps(documents)
        index = index_for_timestamp('ev-', event_time)
        body = f'{index} event {id} {5} {documents}'
        print(body)
        try:
            r = http.request('PUT', f'{lr_url}/logs/hl/hl/{id}/_bulkr', body=body, headers=headers)
            if r.status // 100 != 2:
                print(f'LR request failed: status={r.status} body={body} resp={r.data.decode()}')
            else:
                print("request sent to LR")
        except Exception as err:
            print(f'LR request failed: body={body} error={err}')



def create_es_document(gsp, cluster_name, cluster_uid, job):
    document = {}
    document['eventName'] = "CICDUsageEvent"
    document['eventType'] = "CICD"
    document['eventNumber'] = pr_number
    document['eventTime'] = int(time.time() * 1000)
    document['entityGroup'] = 2
    document['entityType'] = 2
    document['entityId'] = gsp
    document['user'] = "null"
    document['queue'] = "null"
    document['clusterName'] = cluster_name
    document['clusterUid'] = cluster_uid
    document['staticRank'] = 2
    document['dynamicRank'] = 2
    document['title'] = "CICD Document"
    document['detail'] = "detail_dict"
    document['actions'] = "null"
    document['job'] = job
    return document




def create_comments_with_markdown(mk_list):
    comments = ""
    for mk in mk_list:
        comments += "\n----\n"
        comments += "<details>\n"
        comments += "<summary> <h2><b>{}</b></h2></summary>\n\n".format(mk['key'])
        comments += "\n"
        comments += mk['mk']
        comments += "\n"
        comments += "</details>\n\n"
    return comments



# %%
def main():
    mk_list = []
    import requests
    import json

    url = "http://44.207.116.9:5001/api/v1/cicd"

    payload = json.dumps({
    "session": "0724-231054-j6y8msjz/app-20240724231513-0000",
    "pr_number": pr_number
    })
    headers = {
    'Content-Type': 'application/json'
    }

    response = requests.request("POST", url, headers=headers, data=payload)

    for key, value in response.json().items():
        if key != "code_lines":
            mk_list.append({"key":key, "mk": base64.b64decode(value).decode('utf-8')})
        else:
            url = f'https://api.github.com/repos/{repo_name}/pulls/{pr_number}/comments'

            # Request headers
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/vnd.github.v3+json',
                'X-GitHub-Api-Version': '2022-11-28'
            }
            print(perform_code_review(get_file_name_flag=True))
            data = {
                'body': "Replace toPandas() with Spark distributed DataFrames using pandas_api() to avoid collecting all data at the driver. \n pandas_df = PandasOnSparkDF(df1)",
                'path': perform_code_review(get_file_name_flag=True)[0],
                'commit_id': pr_commit_id,
                'line': 36
            }

            # Send POST request
            response = requests.post(url, headers=headers, data=json.dumps(data))

    if True:
        # unravel_comments = re.sub(cleanRe, '', json.dumps(job_run_result_list, indent=4))
        unravel_comments = create_comments_with_markdown(mk_list)

        url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {"body": "{}".format(unravel_comments)}
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()

        channel = "#cicd-notifications"
        # Replace with your Markdown-formatted message
        message = "Unravel has insights for the pr number {} which was raised to merge {} from {} to {}. Click this link for further details {}".format(
            pr_number, pr_commit_id, pr_base_branch, pr_target_branch, pr_url
        )
        # Format the user IDs with '@' symbol
        user_ids = get_pr_reviewers_list()
        formatted_user_ids = ["@" + user_id for user_id in user_ids]

        # Create the message text with user mentions
        message_with_mentions = message + " " + " ".join(formatted_user_ids)

        try:
            # send_markdown_to_slack(channel, message_with_mentions)
            notification_sent = True
        except:
            notification_sent = False

    else:
        print("Nothing to do without Unravel integration")
        sys.exit(0)


# %%
if __name__ == "__main__":
    main()
