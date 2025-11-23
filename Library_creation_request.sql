CREATE SCHEMA IF NOT EXISTS `library_db` DEFAULT CHARACTER SET utf8mb4;

USE library_db;

CREATE TABLE IF NOT EXISTS authors (
  id_author INT NOT NULL AUTO_INCREMENT,
  author_lastname VARCHAR(50) NOT NULL,
  author_name VARCHAR(50) NOT NULL,
  author_patronymic VARCHAR(50) NULL DEFAULT NULL,
  PRIMARY KEY (id_author)
);

CREATE TABLE IF NOT EXISTS books (
  id_book INT NOT NULL AUTO_INCREMENT,
  book_title VARCHAR(50) NOT NULL,
  PRIMARY KEY (id_book)
);

CREATE TABLE IF NOT EXISTS works (
  id_work INT NOT NULL AUTO_INCREMENT,
  work_name VARCHAR(50) NOT NULL,
  work_type VARCHAR(50) NOT NULL,
  PRIMARY KEY (id_work)
);

CREATE TABLE IF NOT EXISTS contents (
  id_book INT NOT NULL,
  id_work INT NOT NULL,
  book_sequence_number INT NOT NULL,
  PRIMARY KEY (id_book, id_work),
  INDEX (id_work),
  FOREIGN KEY (id_book) REFERENCES books (id_book) ON DELETE CASCADE,
  FOREIGN KEY (id_work) REFERENCES works (id_work) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workauthors (
  id_work INT NOT NULL,
  id_author INT NOT NULL,
  PRIMARY KEY (id_work, id_author),
  INDEX (id_author),
  FOREIGN KEY (id_work) REFERENCES works (id_work) ON DELETE CASCADE,
  FOREIGN KEY (id_author) REFERENCES authors (id_author) ON DELETE CASCADE
);