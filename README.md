# Pehuajó Reporta

Plataforma web para reportar problemas en la vía pública de Pehuajó mediante un mapa, con gestión privada de los reportes por parte del equipo.

## MVP acordado

- Carga pública de reportes sin necesidad de crear una cuenta.
- Solicitar al menos un dato de contacto: nombre o email.
- Consultar los reportes en un mapa público.
- Mostrar públicamente ubicación, categoría y estado; mantener descripción, fotos y datos de contacto privados para el equipo.
- Publicar un reporte en el mapa solo después de que el equipo lo apruebe.
- Gestionar y revisar los reportes desde el panel privado de Django.
- Usar los estados recibido, en curso y resuelto.
- Enviar confirmación y cambios de estado por email cuando se informe una dirección.
- Dejar el inicio de sesión con Google, Facebook u otros proveedores para una etapa posterior.

## Tecnologías

- Python 3.12 (probado localmente).
- Django 5.2.
- SQLite para desarrollo local; PostgreSQL se configura mediante `DATABASE_URL` para un entorno online.

El mapa usa Leaflet y los mosaicos de OpenStreetMap; para cargar el mapa hace falta conexión a internet. La búsqueda de direcciones y calles consulta Nominatim de OpenStreetMap. Para buscar una esquina (por ejemplo, `Alem y Alfonsín`, `Alem esq. Alfonsín` o `Alem & Alfonsín`), se consultan las geometrías de ambas calles y se calcula su cruce. Al marcar o mover un punto en el mapa, también se consulta su dirección aproximada para mostrarla en el formulario, incluyendo altura y localidad si esos datos están disponibles; no todos los puntos tienen una altura identificable. Las búsquedas se cachean y se espacian para respetar el uso del servicio; las direcciones y coordenadas se envían a OpenStreetMap. Antes de publicar el servicio, revisar las políticas y atribuciones de mapas y geocodificación.

Las páginas públicas heredan su estructura compartida desde `reports/templates/reports/base.html`. La dirección aproximada seleccionada queda guardada con el reporte y se muestra al pasar el cursor sobre un marcador del mapa. Los reportes anteriores que no tengan dirección guardada la consultan al pasar el cursor; esa consulta usa la caché y los límites de Nominatim.

## Preparar el entorno local

Desde la raíz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

La página de inicio queda disponible en <http://127.0.0.1:8000/>, el formulario para enviar un reporte en <http://127.0.0.1:8000/reportar/>, el mapa público en <http://127.0.0.1:8000/mapa/> y el panel administrativo en <http://127.0.0.1:8000/admin/>.

La configuración local usa SQLite, `DEBUG` activado y, por defecto, un backend de email que imprime los mensajes en la terminal. Si no definís `DJANGO_SECRET_KEY`, Django crea una clave local persistente en `.django_secret_key` (excluida de Git) para que las sesiones sigan siendo válidas al reiniciar el servidor. No borres ese archivo si querés conservar las sesiones activas. Si cargás un reporte con email, buscá allí el enlace de confirmación y abrilo; vence en 48 horas. Cuando el equipo cambia el estado operativo del reporte (recibido, en curso o resuelto), se envía un aviso al email confirmado.

Para probar el envío real con Gmail, copiá `.env.example` como `.env` y completá `DJANGO_EMAIL_HOST_USER`, `DJANGO_EMAIL_HOST_PASSWORD` y `DJANGO_DEFAULT_FROM_EMAIL` con tu dirección y contraseña de aplicación. La cuenta de Google necesita tener activada la verificación en dos pasos para crear una contraseña de aplicación; usá esa clave de 16 caracteres, no la contraseña habitual de Gmail. El `.env` está excluido de Git: nunca compartas ni subas ese archivo. Reiniciá el servidor después de guardarlo y probá enviando un reporte con una dirección a la que puedas acceder; debería llegarte el enlace de confirmación. Si no llega, revisá también Spam y la terminal del servidor.

Para un entorno online, Django puede usar PostgreSQL mediante la variable `DATABASE_URL`; si no se define, sigue usando el archivo SQLite local `db.sqlite3`. La URL de la base debe guardarse como variable secreta del proveedor de hosting, no en el repositorio ni en `.env.example`. Antes de lanzar el sitio, crear la base PostgreSQL y ejecutar `python manage.py migrate` en el entorno online. Los reportes de SQLite local no se copian automáticamente: decidir y realizar cualquier migración de datos por separado, con una copia de seguridad y revisando primero si se quieren conservar los datos de prueba. En producción también definir `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False` y `DJANGO_ALLOWED_HOSTS`; no reutilizar la configuración de desarrollo.

El acceso se hace desde **Ingresar**. Las cuentas las crea el equipo en el panel de Django: usuarios comunes (sin permisos de staff) ingresan a las opciones públicas; las cuentas con permiso de staff pueden abrir el panel administrativo. La sesión se renueva con la actividad y vence luego de 30 minutos sin actividad; también se puede cerrar manualmente desde **Salir**.

Desde la página de inicio elegí entre enviar un reporte o ver el mapa público. Para reportar, buscá una dirección, una esquina como `Alem y Alfonsín` (también se acepta `esq.` o `&`) o marcá el lugar haciendo clic en el mapa; las coordenadas se guardan automáticamente y no hace falta ingresarlas. Completá al menos un nombre o un email, una categoría y una descripción. Si ingresás email, el reporte queda pendiente de confirmación hasta que abras el enlace recibido; luego el equipo podrá revisarlo en `/admin/`. Los reportes sin email quedan directamente pendientes de revisión. La foto es opcional (máximo 5 MB). Desde el admin podés aprobarlos o rechazarlos y cambiar su estado. Los reportes aprobados aparecen en <http://127.0.0.1:8000/mapa/>; los reportes con email solo se publican después de confirmar el email. El tema claro/oscuro se recuerda en la página de inicio, el formulario, la confirmación, el mapa y el panel administrativo. Para conservar la preferencia local, abrí siempre la app usando el mismo origen (por ejemplo, no alternes entre `localhost` y `127.0.0.1`).

Después de actualizar el código, aplicá las migraciones con `python manage.py migrate` antes de iniciar la aplicación.

## Estructura inicial

- `config/`: configuración y rutas del proyecto Django.
- `reports/`: formulario público, modelo y administración de reportes.

Los avisos de cambios de estado solo se envían a reportes con email confirmado; los cambios de revisión (aprobar o rechazar) no modifican el estado operativo ni generan ese aviso.
