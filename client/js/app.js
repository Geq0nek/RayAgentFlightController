function showPage(pageId) {
    document.querySelectorAll('.page-view').forEach(p => {
        p.classList.remove('active');
    });

    document.querySelectorAll('nav button').forEach(b => {
        b.classList.remove('active');
    });

    const selectedPage = document.getElementById('page-' + pageId);
    const selectedBtn = document.getElementById('btn-' + pageId);

    if (selectedPage && selectedBtn) {
        selectedPage.classList.add('active');
        selectedBtn.classList.add('active');
    }

    if (pageId === 'radar') {
        setTimeout(() => {
            mapRadar.map.invalidateSize();
        }, 100);
    }
}
