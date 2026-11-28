INSERT INTO departments (id, name) VALUES
    (1, 'Mathematics'),
    (2, 'Computer Science'),
    (3, 'Physics');
INSERT INTO students (id, name, year, department_id) VALUES
    (1, 'Alina', 1, 1),
    (2, 'Denis', 2, 2),
    (3, 'Ilya', 2, 2),
    (4, 'Katya', 3, 1),
    (5, 'Lev', 1, 3),
    (6, 'Maria', 4, 2),
    (7, 'Nikita', 3, 3);
INSERT INTO courses (id, title, credits, department_id) VALUES
    (1, 'Calculus', 6, 1),
    (2, 'Linear Algebra', 5, 1),
    (3, 'Databases', 4, 2),
    (4, 'Algorithms', 5, 2),
    (5, 'Mechanics', 4, 3);
INSERT INTO enrollments (student_id, course_id, grade) VALUES
    (1, 1, 4), (1, 2, 5),
    (2, 3, 5), (2, 4, 3),
    (3, 3, 4), (3, 4, NULL),
    (4, 1, 5), (4, 2, 4), (4, 4, 4),
    (5, 1, 3), (5, 5, 5),
    (6, 3, 5), (6, 4, 5),
    (7, 5, NULL);
