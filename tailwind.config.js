// CSS do sistema, compilado uma vez (antes era o Tailwind "Play CDN", que
// baixava ~300 KB de JavaScript e compilava o CSS no navegador a cada página).
//
// Depois de mexer em classes dos templates, recompile:
//   npx tailwindcss@3.4.17 -c tailwind.config.js -i core/static_src/entrada.css -o core/static/core/css/app.css --minify
//   python manage.py collectstatic --noinput
module.exports = {
    content: [
        "./core/templates/**/*.html",
        "./core/static/core/js/**/*.js",
    ],
    theme: { extend: {} },
    plugins: [],
};
