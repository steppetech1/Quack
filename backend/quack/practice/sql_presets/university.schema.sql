-- `university`: students, courses and grades (read-only preset of the code sandbox).
CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    year INTEGER NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id)
);
CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id)
);
CREATE TABLE enrollments (
    student_id INTEGER NOT NULL REFERENCES students(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    grade INTEGER,
    PRIMARY KEY (student_id, course_id)
);
