import dash
import dash_bootstrap_components as dbc


app = dash.Dash(__name__, external_stylesheets=[dbc.themes.SANDSTONE],
                title='YH-QChem DB',
                update_title="Loading...",
                meta_tags=[
                    {"name": "image",
                     "property": "og:image",
                     "content": "assets/acq_thumb.png"},
                    {"name": "description",
                     "property": "og:description",
                     "content": "Auto-QChem is an automated workflow for the generation, storage, and retrieval of Density Functional Theory calculations for organic molecules."},
                    {"name": "author",
                     "content": "Eunjoo Lee"
                     }
                ])
server = app.server
app.config.suppress_callback_exceptions = True

app.scripts.config.serve_locally = True
#app.scripts.append_script({
#    'external_url': 'http://127.0.0.1/assets/async_src.js'
#})
#app.scripts.append_script({
#    'external_url': 'http://10.31.51.19:32489/assets/gtag.js'
#})
