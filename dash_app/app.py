import dash
import dash_bootstrap_components as dbc


app = dash.Dash(__name__, external_stylesheets=[dbc.themes.SANDSTONE],
                title='Auto-QChem DB',
                update_title="Loading...",
                meta_tags=[
                    {"name": "image",
                     "property": "og:image",
                     "content": "http://10.31.51.19:32489/assets/acq_thumb.png"},
                    {"name": "description",
                     "property": "og:description",
                     "content": "Auto-QChem is an automated workflow for the generation, storage, and retrieval of Density Functional Theory calculations for organic molecules."},
                    {"name": "author",
                     "content": "Andrzej Żurański"
                     }
                ])
server = app.server
app.config.suppress_callback_exceptions = True

app.scripts.config.serve_locally = False
app.scripts.append_script({
    'external_url': 'http://10.31.51.19:3248/assets/async_src.js'
})
app.scripts.append_script({
    'external_url': 'http://10.31.51.19:32489/assets/gtag.js'
})

if __name__ == '__main__':
    app.run_server(host='0.0.0.0', port = 8050, debug=False)