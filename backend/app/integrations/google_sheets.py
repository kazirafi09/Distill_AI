from urllib.parse import quote

from app.integrations.google_drive import drive_client, validate_drive_id


SHEETS_API_URL = "https://sheets.googleapis.com/v4/spreadsheets"


def read_response_sheet(spreadsheet_id: str, sheet_id: int) -> dict:
    validate_drive_id(spreadsheet_id)
    if sheet_id < 0:
        raise ValueError("Invalid spreadsheet tab ID.")

    with drive_client() as client:
        response = client.get(
            f"{SHEETS_API_URL}/{spreadsheet_id}",
            params={
                "fields": "spreadsheetId,sheets(properties(sheetId,title))",
            },
        )
        response.raise_for_status()
        spreadsheet = response.json()

        tab = next(
            (
                item["properties"]
                for item in spreadsheet.get("sheets", [])
                if item["properties"]["sheetId"] == sheet_id
            ),
            None,
        )
        if tab is None:
            raise ValueError("The response tab was not found.")

        # Resolve the current title from the stable tab ID.
        title = tab["title"]
        sheet_range = "'" + title.replace("'", "''") + "'"

        response = client.get(
            f"{SHEETS_API_URL}/{spreadsheet_id}/values/"
            f"{quote(sheet_range, safe='')}",
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": "FORMATTED_VALUE",
            },
        )
        response.raise_for_status()
        values = response.json().get("values", [])

    if not values or not any(str(cell).strip() for cell in values[0]):
        raise ValueError("The response tab has no column headers.")

    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet_id": sheet_id,
        "title": title,
        "headers": [str(cell).strip() for cell in values[0]],
        "rows": [
            {
                "row_number": row_number,
                "values": row,
            }
            for row_number, row in enumerate(values[1:], start=2)
            if any(str(cell).strip() for cell in row)
        ],
    }