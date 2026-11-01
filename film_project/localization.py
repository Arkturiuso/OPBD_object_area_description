GENRE_RU = {
    "Action": "Боевик",
    "Adventure": "Приключения",
    "Animation": "Анимация",
    "Children": "Детский",
    "Comedy": "Комедия",
    "Crime": "Криминал",
    "Documentary": "Документальный",
    "Drama": "Драма",
    "Fantasy": "Фэнтези",
    "Film-Noir": "Нуар",
    "Horror": "Ужасы",
    "IMAX": "IMAX",
    "Musical": "Мюзикл",
    "Mystery": "Детектив",
    "Romance": "Романтика",
    "Sci-Fi": "Фантастика",
    "Thriller": "Триллер",
    "War": "Военный",
    "Western": "Вестерн",
}


def genre_ru(genres: str) -> str:
    """'Action|Comedy' -> 'Боевик, Комедия'"""
    if not genres:
        return ""
    return ", ".join(GENRE_RU.get(g, g) for g in str(genres).split("|") if g)
