"""This module contains the main process of the robot."""

import json
from dataclasses import dataclass
import os
from datetime import datetime, timedelta

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from itk_dev_shared_components.sap import multi_session, fmcacov, opret_kundekontakt
from itk_dev_shared_components.graph import authentication as graph_authentication
from itk_dev_shared_components.graph import mail as graph_mail
from itk_dev_shared_components.smtp import smtp_util
from bs4 import BeautifulSoup

from robot_framework import config


@dataclass
class Task:
    id_list: list[str]
    sender_az: str
    sender_email: str
    mail: graph_mail.Email


def process(orchestrator_connection: OrchestratorConnection) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")

    graph_credentials = orchestrator_connection.get_credential(config.GRAPH_API)
    graph_access = graph_authentication.authorize_by_username_password(graph_credentials.username, **json.loads(graph_credentials.password))
    multi_session.spawn_sessions(6)

    tasks = get_emails(graph_access)

    approved_senders = json.loads(orchestrator_connection.process_arguments)["approved_senders"]

    for task in tasks:
        error = validate_task(task, approved_senders)
        if error:
            smtp_util.send_email(
                receiver=task.sender_email,
                sender="itk-rpa@mkb.aarhus.dk",
                subject="Fejl i bestilling af Fritagelse for leverandørmodregning",
                body=f"Din bestilling er desværre blevet afvist pga. følgende fejl:\n{error}\n\nVenlig hilsen\nRobotten",
                smtp_port=config.SMTP_PORT,
                smtp_server=config.SMTP_SERVER
            )
        else:
            new_date = _get_new_date()

            # Run all ids in parallel
            args = tuple((id_, new_date, task.sender_az) for id_ in task.id_list)
            multi_session.run_batches(handle_task, args=args, num_sessions=6)

            smtp_util.send_email(
                receiver=task.sender_email,
                sender="itk-rpa@mkb.aarhus.dk",
                subject="Kvittering for Fritagelse for leverandørmodregning",
                body=f"Din bestilling af fritagelse for leverandørmodregning er blevet behandlet.\nFritagelsen er sat indtil {new_date.strftime('%d/%m/%Y')}\n\nVenlig hilsen\nRobotten",
                smtp_port=config.SMTP_PORT,
                smtp_server=config.SMTP_SERVER
            )

        graph_mail.delete_email(task.mail, graph_access)


def handle_task(session, id_: str, new_date: datetime, sender_az: str):
    """Handle a single SAP task.

    Args:
        session: The SAP session
        id_: The cpr or cvr of the fp.
        new_date: The new date to set.
        sender_az: The az-id of the task sender.
    """
    date_changed = set_modregning_date(session, id_, new_date)
    if date_changed:
        note_text = f"RPA: Dato for fritagelse for leverandørmodregning er sat til {new_date.strftime('%d.%m.%Y')} på vegne af {sender_az}."
        opret_kundekontakt.opret_kundekontakter(session, fp=id_, aftaler=None, art="Orientering", notat=note_text)


def get_emails(graph_access: graph_authentication.GraphAccess) -> list[Task]:
    """Read the inbox for the robot and interpret the emails waiting there.

    Args:
        graph_access: The GraphAccess object used to access Graph.

    Returns:
        A list of Task objects. One per email in the inbox.
    """
    mails = graph_mail.get_emails_from_folder("itk-rpa@mkb.aarhus.dk", config.MAIL_SOURCE_FOLDER, graph_access)
    mails = [mail for mail in mails if mail.sender == "noreply@aarhus.dk" and mail.subject == config.MAIL_INBOX_SUBJECT]
    mails.reverse()

    tasks = []

    for mail in mails:
        soup = BeautifulSoup(mail.body, 'html.parser')

        id_paragraph = soup.find_all('p')[0]
        id_list = id_paragraph.get_text(separator="#").split("#")[1:]

        email_tag = soup.find('a', href=True, string=lambda text: text and '@' in text)
        email = email_tag.get_text()
        az_ident = email_tag.find_next_sibling(string=True).strip().split(': ')[1]

        tasks.append(
            Task(id_list=id_list, sender_az=az_ident, sender_email=email, mail=mail)
        )

    return tasks


def validate_task(task: Task, approved_senders: list[str]) -> str | None:
    """Validate a Task against a set of rules.
    Return an error message if any.

    Args:
        task: The Task object to validate.
        approved_senders: A list of approved senders.

    Returns:
        An error message if any.
    """
    if task.sender_az not in approved_senders:
        return "Du er ikke på listen over godkendte brugere."

    for id_ in task.id_list:
        if not (id_.isdigit() and len(id_) in (8, 10)):
            return f"Ugyldigt CPR/CVR-nummer: {id_}"

    return None


def set_modregning_date(session, id_: str, new_date: datetime) -> bool:
    """Set the date for modregningsfritagelse.
    If the date is already set to a date further away, the date is not changed.

    Args:
        session: The sap session.
        id_: The cpr or cvr to set the date for.
        new_date: The new date to set.

    Returns:
        True if the date was changed.
    """
    fmcacov.open_forretningspartner(session, id_)

    session.findById("wnd[0]/shellcont/shell").nodeContextMenu("GP0000000001")
    session.findById("wnd[0]/shellcont/shell").selectContextMenuItem("BPC")
    session.findById(
        "wnd[0]/usr/subSCREEN_3000_RESIZING_AREA:SAPLBUS_LOCATOR:2000/subSCREEN_1010_RIGHT_AREA:SAPLBUPA_DIALOG_JOEL:1000/ssubSCREEN_1000_WORKAREA_AREA:SAPLBUPA_DIALOG_JOEL:1100/ssubSCREEN_1100_MAIN_AREA:SAPLBUPA_DIALOG_JOEL:1101/tabsGS_SCREEN_1100_TABSTRIP/tabpSCREEN_1100_TAB_05"
        ).select()
    date_field = session.findById("wnd[0]/usr/subSCREEN_3000_RESIZING_AREA:SAPLBUS_LOCATOR:2000/subSCREEN_1010_RIGHT_AREA:SAPLBUPA_DIALOG_JOEL:1000/ssubSCREEN_1000_WORKAREA_AREA:SAPLBUPA_DIALOG_JOEL:1100/ssubSCREEN_1100_MAIN_AREA:SAPLBUPA_DIALOG_JOEL:1101/tabsGS_SCREEN_1100_TABSTRIP/tabpSCREEN_1100_TAB_05/ssubSCREEN_1100_TABSTRIP_AREA:SAPLBUSS:0028/ssubGENSUB:SAPLBUSS:7138/subA07P01:SAPLZDKD_AGR_BPMASTER:0100/ctxtBUT000-ZZOFFSET_EXEMP_DATE")

    # Check if the current date (if any) is before the new date
    if not date_field.text or datetime.strptime(date_field.text, "%d.%m.%Y").date() < new_date.date():
        date_field.text = new_date.strftime("%d.%m.%Y")
        session.findById("wnd[0]/tbar[0]/btn[11]").press()
        date_changed = True
    else:
        date_changed = False

    session.findById("wnd[0]/tbar[0]/btn[3]").press()

    return date_changed


def _get_new_date() -> datetime:
    """Calculate the new date for modregningsfritagelse.
    It's today's date plus two days.

    Returns:
        The new date.
    """
    return (datetime.today() + timedelta(days=2))


if __name__ == '__main__':
    conn_string = os.getenv("OpenOrchestratorConnString")
    crypto_key = os.getenv("OpenOrchestratorKey")
    oc = OrchestratorConnection("Fritagelse for leverandørmodregning", conn_string, crypto_key, '{"approved_senders":["az12345"]}')
    process(oc)