# Nothing TV wallpaper pipeline

Feed público para el launcher Nothing TV. Python 3.12, sin dependencias externas.

## Primera ejecución sin claves
Actions → Update wallpapers → Run workflow. Publica doce imágenes reales seleccionadas y etiquetadas MUESTRA; no se presentan como tendencias actuales. El generador verifica sus URLs en w780 y conserva los metadatos planos.

El resultado se sirve en `https://raw.githubusercontent.com/crisyov/crisyov-nothing-tv-wallpapers/wallpapers/wallpapers.json`.

## Activar actualización de fuentes
Añadir Secrets de Actions: TMDB_API_KEY, RAWG_API_KEY y PIXABAY_API_KEY. Se admiten por separado. TMDB aporta cine/series, RAWG juegos y Pixabay los cuatro temas. Objetivo con todas las fuentes: cincuenta objetos mezclados.

El cron ejecuta el workflow a las 08:00 UTC; también hay ejecución manual. Una fuente fallida conserva sus entradas anteriores. Si fallan todas las fuentes configuradas, no se sustituye el feed publicado.

## Almacenamiento
main contiene el generador. wallpapers contiene un único commit sin padres con wallpapers.json y los medios de Pixabay necesarios. La publicación usa force-with-lease sólo en wallpapers; no reescribe main. Los medios proceden de webformatURL, con tamaños hasta 1280×720 y 3 MiB; no se solicitan originales. La recolección física de objetos antiguos depende de GitHub.

## Ejecutar y probar
```sh
python -m unittest discover -s scripts/tests -v
python scripts/fetch_wallpapers.py --verify-images
```

## Fuentes y créditos
This product uses the TMDB API but is not endorsed or certified by TMDB.
TMDB: https://www.themoviedb.org — https://developer.themoviedb.org/docs/faq
RAWG: https://rawg.io
Pixabay y sus autores: https://pixabay.com — https://pixabay.com/api/docs/
Se utiliza Pixabay, que requiere alojamiento propio para imágenes permanentes. No se utiliza la API de Unsplash para esta aplicación de fondos.

Las claves se guardan sólo en GitHub Secrets. Nunca se publican en el JSON ni se incluyen en el APK.
