import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import locale

# Set the locale to Dutch
locale.setlocale(locale.LC_TIME, 'nl_NL.UTF-8')

# Function to check if a given date is a Monday, Tuesday, Thursday, or Friday
def is_valid_day(date):
    return date.weekday() in [0, 1, 3, 4]

# The URL of the page
url = "https://order.hanssens.be/menu/C32A/GBS-Melle-De-Parkschool"

# Send a GET request to the URL
response = requests.get(url)

# Check if the request was successful (status code 200)
if response.status_code == 200:
    # Parse the HTML content of the page
    soup = BeautifulSoup(response.content, 'html.parser')

    # Create HTML content
    html_content = "<!DOCTYPE html><html><head><title>Menu for the week</title><link rel='stylesheet' type='text/css' href='style.css'></head><body>"

    # Get current date
    current_date_obj = datetime.now()

    # Iterate over the next 5 dates (including the current date)
    for i in range(5):
        next_date = current_date_obj + timedelta(days=i)
        if is_valid_day(next_date):
            # Find the day element for the current date
            day_element = soup.find("div", class_="day", attrs={"data-mob-scroll-target": next_date.strftime("%d-%m-%Y")})
            if day_element:
                # Extract the day of the week and date
                day_info = day_element.find("div", class_="day__header--info")
                day_of_week = day_info.find("div", class_="day__header--day").text.strip()
                date_text = day_info.find("div", class_="day__header--date").text.strip()

                # Construct the date with the current year
                header_date = datetime.strptime(f"{date_text} {current_date_obj.year}", "%d %B %Y")

                # Extract the menu items for soup and menu
                soup_items = day_element.find("div", class_="day__content--soup")
                menu_items = day_element.find("div", class_="day__content--menu")

                # Add date as heading
                html_content += f"<h1>{day_of_week}, {header_date.strftime('%d %B %Y')}</h1>"

                # Add menu items or "Geen School" if no items are found
                if soup_items or menu_items:
                    html_content += "<ul>"
                    if soup_items:
                        for item in soup_items.find_all("div", class_="day__content--item"):
                            html_content += f"<li><h2>{item.text}</h2></li>"
                    if menu_items:
                        for item in menu_items.find_all("div", class_="day__content--item"):
                            html_content += f"<li><h2>{item.text}</h2></li>"
                    html_content += "</ul>"
                else:
                    html_content += "<h2>Geen Middagmaal</h2>"

    # Close HTML content
    html_content += "</body></html>"

    # Save the HTML output to a file
    try:
        with open("index.html", "w", encoding="utf-8") as file:
            file.write(html_content)
        print("HTML file saved as index.html")
    except Exception as e:
        print("Error:", e)
else:
    print("Failed to retrieve the menu.")
